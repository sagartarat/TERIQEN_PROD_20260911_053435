function updateKpis() {
        document.getElementById("kpi_view").innerText = document.querySelectorAll(".can-view:checked").length;
        document.getElementById("kpi_add").innerText = document.querySelectorAll(".can-add:checked").length;
        document.getElementById("kpi_edit").innerText = document.querySelectorAll(".can-edit:checked").length;
        document.getElementById("kpi_delete").innerText = document.querySelectorAll(".can-delete:checked").length;
    }

    document.addEventListener("change", function (e) {
        if (e.target.classList.contains("access-check")) {
            const row = e.target.closest("tr");

            if (
                e.target.classList.contains("can-add") ||
                e.target.classList.contains("can-edit") ||
                e.target.classList.contains("can-delete")
            ) {
                if (e.target.checked) {
                    row.querySelector(".can-view").checked = true;
                }
            }

            updateKpis();
        }
    });

    function clearAccess() {
        document.querySelectorAll(".access-check").forEach(chk => chk.checked = false);
        document.getElementById("access_template").value = "";
        updateKpis();
    }

    function selectViewOnly() {
        document.querySelectorAll("tr[data-screen-code]").forEach(row => {
            row.querySelector(".can-view").checked = true;
            row.querySelector(".can-add").checked = false;
            row.querySelector(".can-edit").checked = false;
            row.querySelector(".can-delete").checked = false;
        });
        updateKpis();
    }

    function selectFullAccess() {
        document.querySelectorAll(".access-check").forEach(chk => chk.checked = true);
        updateKpis();
    }

    function applyTemplate() {
        const template = document.getElementById("access_template").value;
        clearAccess();
        document.getElementById("access_template").value = template;

        if (template === "ADMIN") {
            selectFullAccess();
            return;
        }

        if (template === "VIEW_ONLY") {
            selectViewOnly();
            return;
        }

        const templateMap = {
            "OPERATIONS": [
                "OPERATION_QUEUE",
                "WHATSAPP_STUB",
                "PATIENT_CREATION",
                "PATIENT_VIEW_MODIFY",
                "LEAD_GENERATION",
                "WORKFLOW_STATUS_CHANGE",
                "OPERATIONAL_REPORTS"
            ],
            "FINANCE": [
                "INVOICE_GENERATION",
                "INVOICE_PRINTING",
                "INVOICE_RECONCILIATION",
                "AGENT_PAYMENT",
                "INVOICE_MANAGEMENT_REPORTS",
                "AGENT_PAYMENT_REPORTS"
            ],
            "REPORTS": [
                "OPERATIONAL_REPORTS",
                "INVOICE_MANAGEMENT_REPORTS",
                "AGENT_PAYMENT_REPORTS",
                "SLA_REPORTS"
            ]
        };

        const allowedScreens = templateMap[template] || [];

        allowedScreens.forEach(code => {
            const row = document.querySelector(`tr[data-screen-code="${code}"]`);
            if (row) {
                row.querySelector(".can-view").checked = true;
                row.querySelector(".can-add").checked = template !== "REPORTS";
                row.querySelector(".can-edit").checked = template !== "REPORTS";
                row.querySelector(".can-delete").checked = false;
            }
        });

        updateKpis();
    }

    function collectAccessPayload() {
        const userCode = document.getElementById("user_code").value;

        if (!userCode) {
            alert("Please select a user.");
            return null;
        }

        const access = [];

        document.querySelectorAll("tr[data-screen-code]").forEach(row => {
            access.push({
                screen_code: row.dataset.screenCode,
                can_view: row.querySelector(".can-view").checked ? "Y" : "N",
                can_add: row.querySelector(".can-add").checked ? "Y" : "N",
                can_edit: row.querySelector(".can-edit").checked ? "Y" : "N",
                can_delete: row.querySelector(".can-delete").checked ? "Y" : "N"
            });
        });

        return {
            user_code: userCode,
            access: access
        };
    }

    function saveAccess() {
        const payload = collectAccessPayload();
        if (!payload) return;

        fetch("/api/system-control/user-screen-access/save", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(payload)
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                alert("User screen access saved successfully.");
            } else {
                alert(data.message || "Failed to save user screen access.");
            }
        })
        .catch(err => {
            console.error(err);
            alert("Error while saving user screen access.");
        });
    }

    function loadAccess() {
        const userCode = document.getElementById("user_code").value;

        if (!userCode) {
            alert("Please select a user.");
            return;
        }

        fetch(`/api/system-control/user-screen-access/${encodeURIComponent(userCode)}`)
        .then(res => res.json())
        .then(data => {
            clearAccess();

            if (!data.success) {
                alert(data.message || "No access data found for selected user.");
                return;
            }

            (data.access || []).forEach(item => {
                const row = document.querySelector(`tr[data-screen-code="${item.screen_code}"]`);
                if (row) {
                    row.querySelector(".can-view").checked = item.can_view === "Y";
                    row.querySelector(".can-add").checked = item.can_add === "Y";
                    row.querySelector(".can-edit").checked = item.can_edit === "Y";
                    row.querySelector(".can-delete").checked = item.can_delete === "Y";
                }
            });

            updateKpis();
        })
        .catch(err => {
            console.error(err);
            alert("Error while loading user screen access.");
        });
    }

    updateKpis();
