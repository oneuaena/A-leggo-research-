; Opens the LAN frontend port so phones on the same Wi-Fi can reach the
; runtime.  The installer runs elevated, therefore netsh succeeds here even
; though the sidecar itself stays unprivileged.
!macro NSIS_HOOK_POSTINSTALL
  nsExec::ExecToLog 'netsh advfirewall firewall delete rule name="A-leggo Next LAN"'
  nsExec::ExecToLog 'netsh advfirewall firewall add rule name="A-leggo Next LAN" dir=in action=allow protocol=TCP localport=3782 enable=yes profile=any'
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::ExecToLog 'netsh advfirewall firewall delete rule name="A-leggo Next LAN"'
!macroend
