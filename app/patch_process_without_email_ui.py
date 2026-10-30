from pathlib import Path

html_file = Path("app/templates/operationqueue.html")
js_file = Path("app/static/js/operationqueue.js")


# ============================================================
# 1. MATCH BUTTON COLOUR WITH EMAIL BUTTON
# ============================================================

text = html_file.read_text(encoding="utf-8")

old = '''                    class="opq-btn opq-btn-sm"
                    type="button"
                    id="opqProcessWithoutEmailBtn"'''

new = '''                    class="opq-btn opq-btn-primary opq-btn-sm"
                    type="button"
                    id="opqProcessWithoutEmailBtn"'''

if old not in text:
    raise RuntimeError(
        "Could not locate Process Without Email button."
    )

text = text.replace(old, new, 1)

html_file.write_text(text, encoding="utf-8")


# ============================================================
# 2. DISABLE PROCESS WITHOUT EMAIL WHEN APPROPRIATE
# ============================================================

text = js_file.read_text(encoding="utf-8")

old = '''    emailBtn.title = closed
      ? "Email communication is disabled because this channel thread is CLOSED."
      : alreadySentSameRequest
        ? `Email communication is disabled because email is already SENT${templateName ? ` using template ${templateName}` : ""} for this request.`
        : "Open Email Communication Window";
  }'''

new = '''    emailBtn.title = closed
      ? "Email communication is disabled because this channel thread is CLOSED."
      : alreadySentSameRequest
        ? `Email communication is disabled because email is already SENT${templateName ? ` using template ${templateName}` : ""} for this request.`
        : "Open Email Communication Window";

    // Process Without Email is only a valid action for an
    // open case whose Patient / Workflow ingestion is not complete.
    const noEmailBtn = document.getElementById(
      "opqProcessWithoutEmailBtn"
    );

    if(noEmailBtn){
      const flagIsTrue = (value) => {
        const normalized = safeValue(value)
          .trim()
          .toLowerCase();

        return [
          "1",
          "true",
          "yes",
          "y"
        ].includes(normalized);
      };

      let patientCreated = false;
      let workflowCreated = false;
      let converted = false;

      if(
        rowOrWorkflowStatus &&
        typeof rowOrWorkflowStatus === "object"
      ){
        patientCreated = flagIsTrue(
          rowOrWorkflowStatus.patient_created_flag
        );

        workflowCreated = flagIsTrue(
          rowOrWorkflowStatus.workflow_created_flag
        );

        converted = flagIsTrue(
          rowOrWorkflowStatus.converted_flag
        );
      }

      const ingestionCompleted =
        patientCreated && workflowCreated;

      const noEmailDisabled =
        closed ||
        ingestionCompleted ||
        converted;

      noEmailBtn.disabled = noEmailDisabled;

      noEmailBtn.style.opacity =
        noEmailDisabled ? "0.55" : "";

      noEmailBtn.style.cursor =
        noEmailDisabled ? "not-allowed" : "";

      if(closed){
        noEmailBtn.title =
          "Process Without Email is disabled because this case is CLOSED.";
      }
      else if(ingestionCompleted || converted){
        noEmailBtn.title =
          "Process Without Email is disabled because Patient and Workflow ingestion has already been completed.";
      }
      else{
        noEmailBtn.title =
          "Process this case without sending an email.";
      }
    }
  }'''

if old not in text:
    raise RuntimeError(
        "Could not locate updateEmailLauncherState block."
    )

text = text.replace(old, new, 1)

js_file.write_text(text, encoding="utf-8")

print("PROCESS WITHOUT EMAIL UI RULES APPLIED SUCCESSFULLY")
