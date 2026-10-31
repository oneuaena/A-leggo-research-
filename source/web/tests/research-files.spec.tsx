import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ResearchFilesPanel from "@/features/research/ResearchFilesPanel";

const api = vi.hoisted(() => ({ listResearchWorkspaceFiles: vi.fn(), previewResearchWorkspaceFile: vi.fn(), downloadResearchWorkspaceFile: vi.fn(), openResearchWorkspaceFile: vi.fn() }));
vi.mock("@/lib/research-api", () => api);
const file = { path: "experiments/run-1/files/results.csv", sizeBytes: 14, mediaType: "text/csv", previewKind: "text" };

beforeEach(() => {
  vi.clearAllMocks();
  api.listResearchWorkspaceFiles.mockResolvedValue({ items: [file] });
  api.previewResearchWorkspaceFile.mockResolvedValue({ ...file, text: "n,value\n1,1\n", truncated: false });
  api.openResearchWorkspaceFile.mockResolvedValue({ opened: true });
});

function expand() {
  const details = screen.getByText("研究工作区文件").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
}

describe("real workspace file controls", () => {
  it("loads files on demand, previews actual CSV and opens its containing folder", async () => {
    render(<ResearchFilesPanel projectId="project-1" workspacePath="D:/Research" revision="1" />);
    expect(api.listResearchWorkspaceFiles).not.toHaveBeenCalled();
    expand();
    fireEvent.click(await screen.findByRole("button", { name: "预览 results.csv" }));
    expect(await screen.findByText(/n,value/)).toBeInTheDocument();
    expect(api.previewResearchWorkspaceFile).toHaveBeenCalledWith("project-1", file.path);
    fireEvent.click(screen.getByRole("button", { name: "打开文件夹" }));
    await waitFor(() => expect(api.openResearchWorkspaceFile).toHaveBeenCalledWith("project-1", file.path, false));
  });

  it("refreshes an open file index when a step produces new files and identifies bounded previews", async () => {
    const view = render(<ResearchFilesPanel projectId="project-1" workspacePath="" revision="1" />);
    expand();
    await screen.findByRole("button", { name: "预览 results.csv" });
    api.previewResearchWorkspaceFile.mockResolvedValue({ ...file, text: "actual first chunk", truncated: true });
    fireEvent.click(screen.getByRole("button", { name: "预览 results.csv" }));
    expect(await screen.findByText(/前 256 KiB/)).toBeInTheDocument();
    await act(async () => view.rerender(<ResearchFilesPanel projectId="project-1" workspacePath="" revision="2" />));
    expect(api.listResearchWorkspaceFiles).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("打开文件夹")).not.toBeInTheDocument();
  });

  it("downloads the original file through the connected API", async () => {
    const create = vi.fn().mockReturnValue("blob:download"), revoke = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }));
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    api.downloadResearchWorkspaceFile.mockResolvedValue(new Blob(["n,value\n1,1\n"], { type: "text/csv" }));
    render(<ResearchFilesPanel projectId="project-1" workspacePath="" revision="1" />);
    expand();
    fireEvent.click(await screen.findByRole("button", { name: "下载 results.csv" }));
    await waitFor(() => expect(clicked).toHaveBeenCalled());
    expect(api.downloadResearchWorkspaceFile).toHaveBeenCalledWith("project-1", file.path);
    expect(create).toHaveBeenCalledWith(expect.any(Blob));
    clicked.mockRestore();
  });

  it("does not let an older file index overwrite a newer research revision", async () => {
    let finishOld!: (value: { items: typeof file[] }) => void;
    api.listResearchWorkspaceFiles.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }));
    const view = render(<ResearchFilesPanel projectId="project-1" workspacePath="" revision="1" />);
    expand();
    await waitFor(() => expect(api.listResearchWorkspaceFiles).toHaveBeenCalledTimes(1));
    api.listResearchWorkspaceFiles.mockResolvedValue({ items: [{ ...file, path: "experiments/new/results-new.csv" }] });
    view.rerender(<ResearchFilesPanel projectId="project-1" workspacePath="" revision="2" />);
    await screen.findByRole("button", { name: "预览 results-new.csv" });
    await act(async () => finishOld({ items: [file] }));
    expect(screen.getByRole("button", { name: "预览 results-new.csv" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "预览 results.csv" })).not.toBeInTheDocument();
  });
});
