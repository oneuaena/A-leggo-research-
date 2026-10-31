#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{sync::Mutex, thread, time::{Duration, Instant}};
use tauri::{ipc::CapabilityBuilder, Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_shell::{process::{CommandChild, CommandEvent}, ShellExt};

struct RuntimeChild(Mutex<Option<CommandChild>>);

const FRONTEND_READY_PREFIX: &str = "ALEGGO_FRONTEND_READY_PORT=";

fn parse_frontend_port(line: &str) -> Option<u16> {
    line.trim().strip_prefix(FRONTEND_READY_PREFIX)?.parse::<u16>().ok().filter(|port| *port != 0)
}

fn wait_for_runtime(events: &mut tauri::async_runtime::Receiver<CommandEvent>) -> Result<u16, String> {
    let deadline = Instant::now() + Duration::from_secs(90);
    while Instant::now() < deadline {
        while let Ok(event) = events.try_recv() {
            match event {
                CommandEvent::Stdout(bytes) => {
                    for line in String::from_utf8_lossy(&bytes).lines() {
                        if let Some(port) = parse_frontend_port(line) { return Ok(port); }
                    }
                }
                CommandEvent::Terminated(_) => return Err("A-leggo Next local runtime stopped during startup.".into()),
                _ => {}
            }
        }
        thread::sleep(Duration::from_millis(100));
    }
    Err("A-leggo Next local runtime did not become ready within 90 seconds.".into())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            // This is a real packaged sidecar.  Provider credentials remain in
            // the local settings service and are not available here.
            let (mut events, child) = app
                .shell()
                .sidecar("aleggo-research-server")
                .map_err(|error| error.to_string())?
                .spawn()
                .map_err(|error| error.to_string())?;
            let frontend_port = match wait_for_runtime(&mut events) {
                Ok(port) => port,
                Err(error) => { let _ = child.kill(); return Err(error.into()); }
            };
            app.manage(RuntimeChild(Mutex::new(Some(child))));
            // Drain sidecar output for its entire lifetime so the bounded
            // shell channel cannot block the backend when it produces logs.
            tauri::async_runtime::spawn(async move { while events.recv().await.is_some() {} });
            // Grant IPC only to this launch's exact loopback origin. The sidecar
            // is the single source of truth for settings and environment ports.
            app.add_capability(CapabilityBuilder::new("runtime-loopback")
                .window("main").local(false)
                .remote(format!("http://127.0.0.1:{frontend_port}/*"))
                .permission("core:default").permission("dialog:allow-open"))?;
            let url = format!("http://127.0.0.1:{frontend_port}")
                .parse::<tauri::Url>()
                .map_err(|error| error.to_string())?;
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("A-leggo Research")
                .inner_size(1280.0, 820.0)
                .min_inner_size(900.0, 620.0)
                .build()
                .map_err(|error| error.to_string())?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while starting A-leggo Research")
        .run(|handle, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = handle.try_state::<RuntimeChild>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(child) = guard.take() { let _ = child.kill(); }
                    }
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::parse_frontend_port;

    #[test]
    fn ready_marker_accepts_default_and_configured_ports() {
        assert_eq!(parse_frontend_port("ALEGGO_FRONTEND_READY_PORT=3782"), Some(3782));
        assert_eq!(parse_frontend_port("ALEGGO_FRONTEND_READY_PORT=49001\n"), Some(49001));
    }

    #[test]
    fn ready_marker_rejects_hosts_invalid_ports_and_unrelated_output() {
        for value in ["ALEGGO_FRONTEND_READY_PORT=0", "ALEGGO_FRONTEND_READY_PORT=65536",
            "ALEGGO_FRONTEND_READY_PORT=https://example.com", "ALEGGO_FRONTEND_READY_PORT=3782/private",
            "Other output ALEGGO_FRONTEND_READY_PORT=3782"] {
            assert_eq!(parse_frontend_port(value), None);
        }
    }
}
