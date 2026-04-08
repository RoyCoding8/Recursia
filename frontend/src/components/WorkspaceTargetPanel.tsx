"use client";

interface WorkspaceTargetPanelProps {
  selectedFolderName?: string;
  supportsDirectoryPicker: boolean;
  isPicking: boolean;
  error?: string | null;
  onPickFolder: () => Promise<void> | void;
  onClearFolder: () => void;
}

export function WorkspaceTargetPanel({
  selectedFolderName,
  supportsDirectoryPicker,
  isPicking,
  error,
  onPickFolder,
  onClearFolder,
}: WorkspaceTargetPanelProps) {
  return (
    <section className="panel workspacePanel" aria-label="Workspace target panel">
      <div className="panelHeader">
        <div>
          <h2>Target Folder</h2>
          <p className="workspacePanelSubtle">
            Make proposed changes, target folders, and approved writes explicit in one place.
          </p>
        </div>
        <span className="badge">{selectedFolderName ? "selected" : "not selected"}</span>
      </div>

      <div className="workspacePanelGrid">
        <section className="workspaceCard">
          <h3>Current Runtime Behavior</h3>
          <p>
            The backend now stages file proposals relative to <code>workspace/{`{run_id}`}</code>.
            Final writes happen only when you approve them from the frontend review panel.
          </p>
        </section>

        <section className="workspaceCard">
          <h3>Reviewed Apply Target</h3>
          <p>
            {selectedFolderName
              ? <>Selected folder: <strong>{selectedFolderName}</strong></>
              : "No folder selected yet."}
          </p>
          <p className="workspacePanelHint">
            Reviewed proposals are written here only after you choose `Apply selected` or `Apply all`.
          </p>
          <div className="workspaceActions">
            <button
              type="button"
              className="buttonPrimary"
              onClick={() => void onPickFolder()}
              disabled={!supportsDirectoryPicker || isPicking}
            >
              {isPicking ? "Choosing..." : "Choose Folder"}
            </button>
            <button
              type="button"
              className="buttonGhost"
              onClick={onClearFolder}
              disabled={!selectedFolderName || isPicking}
            >
              Clear
            </button>
          </div>
          {!supportsDirectoryPicker ? (
            <p className="messageHint">
              Directory picking is not available in this browser environment. We will need an explicit fallback for reviewed apply.
            </p>
          ) : null}
          {error ? (
            <p className="messageError" role="alert">
              {error}
            </p>
          ) : null}
        </section>
      </div>
    </section>
  );
}
