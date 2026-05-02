from __future__ import annotations


def render_local_folder_import_control() -> str:
    return """
      <form class="import-panel" action="/import/local-folder" method="post" enctype="multipart/form-data" data-local-folder-import-form>
        <div class="import-heading">
          <span class="label">Initial notes import</span>
          <h2>Import notes</h2>
          <p>Choose notes from your computer. They will be copied into your mounted Google Drive folder.</p>
        </div>
        <input class="import-hidden-input" type="file" name="files" multiple data-import-files-input>
        <input class="import-hidden-input" type="file" name="files" webkitdirectory directory multiple data-import-folder-input>
        <div class="import-choice-panel" data-import-choice-panel>
          <section class="import-summary" data-import-state="empty" aria-live="polite">
            <div>
              <p class="import-status">Choose files or a folder to import notes.</p>
              <p class="import-details">Nothing will be copied until you choose what to import.</p>
            </div>
          </section>
          <div class="import-source-grid">
            <button class="import-source-button" type="button" data-import-files-button data-source-mode="files">
              <strong>Choose files</strong>
              <span>Select one or more note files.</span>
            </button>
            <button class="import-source-button" type="button" data-import-folder-button data-source-mode="folder">
              <strong>Choose folder</strong>
              <span>Select one folder and import it recursively.</span>
            </button>
          </div>
        </div>
        <section class="import-summary import-selection-panel hidden" data-import-selection-panel data-import-state="selected" aria-live="polite" hidden>
          <div>
            <p class="import-status" data-import-status>Choose files or a folder to import notes.</p>
            <p class="import-details" data-import-details>Nothing will be copied until you choose files and press Import.</p>
            <fieldset class="import-folder-mode hidden" data-folder-mode-panel hidden>
              <legend>Folder placement</legend>
              <label>
                <input type="radio" name="folder-import-mode" value="create-folder" checked data-folder-mode-input>
                <span>Create selected folder</span>
              </label>
              <label>
                <input type="radio" name="folder-import-mode" value="strip-folder" data-folder-mode-input>
                <span>Import folder contents</span>
              </label>
            </fieldset>
            <div class="actions import-actions">
              <button class="button import-submit" type="submit" data-import-submit>Import</button>
              <button class="import-secondary-button" type="button" data-import-rechoose>Choose different files</button>
            </div>
          </div>
        </section>
        <section class="import-summary import-progress-panel hidden" data-import-progress-panel data-import-state="uploading" aria-live="polite" hidden>
          <span class="import-spinner" data-import-spinner aria-hidden="true"></span>
          <div>
            <p class="import-status" data-import-progress-status>Uploading files to the app...</p>
            <p class="import-details" data-import-progress-details>Keep this page open.</p>
          </div>
        </section>
        <section class="import-summary import-result-panel hidden" data-import-result-panel data-import-state="success" aria-live="polite" hidden>
          <div>
            <p class="import-status" data-import-result-status>Imported files.</p>
            <p class="import-details" data-import-result-details>You can return to the Assistant or import more.</p>
          </div>
          <div class="actions import-actions">
            <button class="button import-submit" type="button" data-import-more>Import more</button>
          </div>
        </section>
      </form>
      <script>
        (() => {
          const form = document.querySelector("[data-local-folder-import-form]");
          if (!form) {
            return;
          }
          const fileInput = form.querySelector("[data-import-files-input]");
          const folderInput = form.querySelector("[data-import-folder-input]");
          const choicePanel = form.querySelector("[data-import-choice-panel]");
          const selectionPanel = form.querySelector("[data-import-selection-panel]");
          const progressPanel = form.querySelector("[data-import-progress-panel]");
          const resultPanel = form.querySelector("[data-import-result-panel]");
          const fileButton = form.querySelector("[data-import-files-button]");
          const folderButton = form.querySelector("[data-import-folder-button]");
          const folderModePanel = form.querySelector("[data-folder-mode-panel]");
          const folderModeInputs = Array.from(form.querySelectorAll("[data-folder-mode-input]"));
          const submitButton = form.querySelector("[data-import-submit]");
          const rechooseButton = form.querySelector("[data-import-rechoose]");
          const status = form.querySelector("[data-import-status]");
          const details = form.querySelector("[data-import-details]");
          const progressStatus = form.querySelector("[data-import-progress-status]");
          const progressDetails = form.querySelector("[data-import-progress-details]");
          const resultStatus = form.querySelector("[data-import-result-status]");
          const resultDetails = form.querySelector("[data-import-result-details]");
          const importMoreButton = form.querySelector("[data-import-more]");
          let selectedFiles = [];
          let selectedSource = null;
          let lastErrorMessage = "";
          let currentImportState = "empty";

          fileButton.addEventListener("click", () => openFilePicker());
          folderButton.addEventListener("click", () => openFolderPicker());
          rechooseButton.addEventListener("click", resetImportFlow);
          importMoreButton.addEventListener("click", resetImportFlow);
          fileInput.addEventListener("change", () => {
            const files = Array.from(fileInput.files || []);
            if (files.length === 0) {
              renderImportState(selectedFiles.length === 0 ? "empty" : currentImportState);
              return;
            }
            folderInput.value = "";
            setSelection("files", files);
          });
          folderInput.addEventListener("change", () => {
            const files = Array.from(folderInput.files || []);
            if (files.length === 0) {
              renderImportState(selectedFiles.length === 0 ? "empty" : currentImportState);
              return;
            }
            fileInput.value = "";
            setSelection("folder", files);
          });
          for (const input of folderModeInputs) {
            input.addEventListener("change", () => {
              renderImportState(lastErrorMessage ? "error" : "selected");
            });
          }
          form.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (selectedFiles.length === 0) {
              renderImportState("empty");
              return;
            }
            lastErrorMessage = "";
            renderImportState("uploading");
            const formData = new FormData();
            for (const file of selectedFiles) {
              const targetPath = targetPathFor(file);
              if (targetPath) {
                formData.append("files", file, targetPath);
              }
            }
            const response = await postImport(formData);
            if (response.ok) {
              renderImportState(
                "success",
                `Imported ${fileCountLabel(selectedFiles.length)}.`,
                "You can return to the Assistant or import more.",
              );
              return;
            }
            lastErrorMessage = userErrorMessage(response.text);
            renderImportState("error");
          });
          renderImportState("empty");

          function openFilePicker() {
            fileInput.value = "";
            fileInput.click();
          }

          function openFolderPicker() {
            folderInput.value = "";
            folderInput.click();
          }

          function setSelection(source, files) {
            selectedSource = source;
            selectedFiles = files;
            lastErrorMessage = "";
            renderImportState(files.length === 0 ? "empty" : "selected");
          }

          function resetImportFlow() {
            selectedFiles = [];
            selectedSource = null;
            lastErrorMessage = "";
            fileInput.value = "";
            folderInput.value = "";
            renderImportState("empty");
          }

          function renderImportState(state, resultStatusText = "", resultDetailText = "") {
            currentImportState = state;
            const hasSelection = selectedFiles.length > 0;
            const isSelectedState = state === "selected" || state === "error";
            const isBusy = state === "uploading" || state === "finalizing";
            setHidden(choicePanel, state !== "empty");
            setHidden(selectionPanel, !isSelectedState);
            setHidden(progressPanel, !isBusy);
            setHidden(resultPanel, state !== "success");
            setHidden(folderModePanel, !(isSelectedState && selectedSource === "folder"));
            submitButton.disabled = !hasSelection || isBusy;
            submitButton.textContent = state === "error" ? "Try import again" : "Import";
            rechooseButton.textContent = selectedSource === "folder" ? "Choose different folder" : "Choose different files";
            fileButton.disabled = isBusy;
            folderButton.disabled = isBusy;
            rechooseButton.disabled = isBusy;
            for (const input of folderModeInputs) {
              input.disabled = isBusy;
            }
            if (state === "selected") {
              selectionPanel.dataset.importState = "selected";
              status.textContent = `Ready to import ${fileCountLabel(selectedFiles.length)}.`;
              details.textContent = selectionDetails();
              return;
            }
            if (state === "error") {
              selectionPanel.dataset.importState = "error";
              status.textContent = lastErrorMessage || "Import failed.";
              details.textContent = `No files were imported. ${selectionDetails()}`;
              return;
            }
            if (state === "uploading") {
              progressPanel.dataset.importState = "uploading";
              progressStatus.textContent = "Uploading files to the app...";
              progressDetails.textContent = "Keep this page open.";
              return;
            }
            if (state === "finalizing") {
              progressPanel.dataset.importState = "finalizing";
              progressStatus.textContent = "Copying files into your Drive folder...";
              progressDetails.textContent = "Google Drive may need a moment to show the new files.";
              return;
            }
            if (state === "success") {
              resultPanel.dataset.importState = "success";
              resultStatus.textContent = resultStatusText || `Imported ${fileCountLabel(selectedFiles.length)}.`;
              resultDetails.textContent = resultDetailText || "You can return to the Assistant or import more.";
            }
          }

          function selectionDetails() {
            const targetDescription = selectedSource === "folder"
              ? folderModeDescription()
              : "Files will be imported into the notes folder root.";
            return `${formatBytes(totalBytes(selectedFiles))}. ${targetDescription}`;
          }

          function setHidden(element, isHidden) {
            element.hidden = isHidden;
            element.classList.toggle("hidden", isHidden);
          }

          function selectedFolderMode() {
            const selected = folderModeInputs.find((input) => input.checked);
            return selected ? selected.value : "create-folder";
          }

          function folderModeDescription() {
            const folderName = selectedFolderName();
            if (selectedFolderMode() === "strip-folder") {
              return `Only the contents of ${folderName} will be imported into the notes folder root.`;
            }
            return `${folderName} will be created in your notes.`;
          }

          function selectedFolderName() {
            const fileWithFolderPath = selectedFiles.find((file) => file.webkitRelativePath);
            const relativePath = fileWithFolderPath ? fileWithFolderPath.webkitRelativePath : "";
            const folderName = relativePath.split("/")[0];
            return folderName || "The selected folder";
          }

          function targetPathFor(file) {
            if (selectedSource === "files") {
              return file.name;
            }
            const relativePath = file.webkitRelativePath || file.name;
            if (selectedFolderMode() === "strip-folder") {
              return relativePath.split("/").slice(1).join("/") || file.name;
            }
            return relativePath;
          }

          function postImport(formData) {
            return new Promise((resolve) => {
              const request = new XMLHttpRequest();
              request.open("POST", form.action);
              request.upload.addEventListener("load", () => {
                renderImportState(
                  "finalizing",
                  "Copying files into your Drive folder...",
                  "Google Drive may need a moment to show the new files.",
                );
              });
              request.addEventListener("load", () => {
                resolve({
                  ok: request.status >= 200 && request.status < 300,
                  text: request.responseText || "",
                });
              });
              request.addEventListener("error", () => {
                resolve({
                  ok: false,
                  text: "Import failed. Check your connection and try again.",
                });
              });
              request.send(formData);
            });
          }

          function userErrorMessage(message) {
            const lower = message.toLowerCase();
            if (lower.includes("already exists")) {
              return "Some selected files already exist in your Drive folder. Remove or rename them, then choose files again.";
            }
            if (lower.includes("path")) {
              return "One selected file has an unsupported path. Choose a different file or folder.";
            }
            if (lower.includes("mounted")) {
              return "Google Drive is not ready yet. Wait until Drive is connected, then try again.";
            }
            return message || "Import failed. Choose files again and retry.";
          }

          function fileCountLabel(count) {
            return count === 1 ? "1 file" : `${count} files`;
          }

          function totalBytes(files) {
            return files.reduce((sum, file) => sum + file.size, 0);
          }

          function formatBytes(bytes) {
            if (bytes < 1024) {
              return `${bytes} B`;
            }
            if (bytes < 1024 * 1024) {
              return `${(bytes / 1024).toFixed(1)} KB`;
            }
            return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
          }
        })();
      </script>"""
