(function () {
    const form = document.getElementById("whatsappStubForm");
    if (!form) {
      return;
    }

    const clearBtn = document.getElementById("clearBtn");
    const statusBox = document.getElementById("statusBox");
    const attachmentsInput = document.getElementById("attachments");

    const previewMobile = document.getElementById("previewMobile");
    const previewMessage = document.getElementById("previewMessage");
    const previewAttachments = document.getElementById("previewAttachments");
    const selectedFilesBox = document.getElementById("selectedFilesBox");
    const selectedFilesList = document.getElementById("selectedFilesList");

    const MAX_TOTAL_SIZE = 10 * 1024 * 1024;

    function showStatus(message, type) {
      statusBox.className = "ws-status " + type;
      statusBox.textContent = message;
    }

    function clearStatus() {
      statusBox.className = "ws-status";
      statusBox.textContent = "";
    }

    function formatBytes(bytes) {
      const value = Number(bytes || 0);

      if (value < 1024) return value + " B";
      if (value < 1024 * 1024) return (value / 1024).toFixed(2) + " KB";
      return (value / (1024 * 1024)).toFixed(2) + " MB";
    }

    function getSelectedFiles() {
      return Array.from((attachmentsInput && attachmentsInput.files) || []);
    }

    function getTotalFileSize(files) {
      return files.reduce(function (sum, file) {
        return sum + (file.size || 0);
      }, 0);
    }

    function renderSelectedFiles() {
      const files = getSelectedFiles();

      if (!files.length) {
        selectedFilesBox.classList.remove("active");
        selectedFilesList.innerHTML = "";
        return;
      }

      const totalSize = getTotalFileSize(files);

      selectedFilesList.innerHTML = files.map(function (file, index) {
        return (
          '<div class="ws-file-item">' +
            (index + 1) + '. ' +
            file.name + ' (' + formatBytes(file.size) + ')' +
          '</div>'
        );
      }).join("");

      selectedFilesList.innerHTML +=
        '<div class="ws-file-item"><strong>Total Size:</strong> ' + formatBytes(totalSize) + '</div>';

      selectedFilesBox.classList.add("active");
    }

    if (attachmentsInput) {
      attachmentsInput.addEventListener("change", function () {
        clearStatus();
        renderSelectedFiles();

        const files = getSelectedFiles();
        const totalSize = getTotalFileSize(files);

        if (totalSize > MAX_TOTAL_SIZE) {
          showStatus("Total attachment size per message cannot exceed 10 MB.", "error");
        }
      });
    }

    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      clearStatus();

      const formData = new FormData(form);
      const mobileNumber = String(formData.get("mobile_number") || "").trim();
      const message = String(formData.get("message") || "").trim();
      const files = getSelectedFiles();
      const totalSize = getTotalFileSize(files);

      if (!message) {
        const liveMessageBox = form.querySelector('textarea[name="message"]');
        if (liveMessageBox) {
          liveMessageBox.focus();
        }
        showStatus("Message is required.", "error");
        return;
      }

      if (totalSize > MAX_TOTAL_SIZE) {
        showStatus("Total attachment size per message cannot exceed 10 MB.", "error");
        return;
      }

      try {
        const response = await fetch("/whatsapp-stub/push", {
          method: "POST",
          body: formData
        });

        const result = await response.json();

        if (!response.ok || !result.success) {
          showStatus(result.message || "Submission failed.", "error");
          return;
        }

        previewMobile.textContent = mobileNumber || "-";
        previewMessage.textContent = message || "-";
        previewAttachments.textContent = files.length
          ? files.map(function (file) { return file.name; }).join(", ")
          : "-";

        showStatus(result.message || "Submitted successfully.", "success");
      } catch (error) {
        console.error("Whatsapp stub submit error:", error);
        showStatus("API call failed. Please check server and route.", "error");
      }
    });

    if (clearBtn) {
      clearBtn.addEventListener("click", function () {
        form.reset();
        clearStatus();
        previewMobile.textContent = "-";
        previewMessage.textContent = "-";
        previewAttachments.textContent = "-";
        selectedFilesBox.classList.remove("active");
        selectedFilesList.innerHTML = "";
      });
    }
  })();
