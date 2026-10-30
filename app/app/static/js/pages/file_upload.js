(function () {
    const form = document.getElementById('fileUploadForm');
    const fileInput = document.getElementById('upload_file');
    const fileNamePreview = document.getElementById('file_name_preview');
    const clearBtn = document.getElementById('clearBtn');
    const uploadBtn = document.getElementById('uploadBtn');

    function updateFilePreview() {
        if (fileInput && fileInput.files && fileInput.files.length > 0) {
            const fileName = fileInput.files[0].name;
            fileNamePreview.value = fileName;
        } else {
            fileNamePreview.value = '';
        }
    }

    if (fileInput) {
        fileInput.addEventListener('change', updateFilePreview);
    }

    if (clearBtn) {
        clearBtn.addEventListener('click', function () {
            form.reset();
            fileNamePreview.value = '';
            form.classList.remove('was-validated');
            uploadBtn.disabled = false;
            uploadBtn.innerHTML = '<i class="bi bi-upload"></i> Upload File';
        });
    }

    if (form) {
        form.addEventListener('submit', function (e) {
            if (!form.checkValidity()) {
                e.preventDefault();
                e.stopPropagation();
            } else {
                uploadBtn.disabled = true;
                uploadBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Uploading...';
            }
            form.classList.add('was-validated');
        });
    }

    updateFilePreview();
})();

