/* ================================
   PRINT / SAVE PDF FUNCTION
================================ */

async function printInvoiceOnly() {
  try {
    const invoiceNo = ((document.getElementById("invoiceTemplatePageRoot") || {}).dataset || {}).invoiceNumber || "";
    if (invoiceNo) {
      const pdfUrl = "/invoice-print-pdf/" + encodeURIComponent(invoiceNo);
      const response = await fetch(pdfUrl, {
        method: "GET",
        credentials: "same-origin"
      });

      if (!response.ok) {
        throw new Error("Unable to generate invoice PDF.");
      }

      const blob = await response.blob();
      const blobUrl = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = blobUrl;
      link.download = invoiceNo + ".pdf";
      document.body.appendChild(link);
      link.click();
      link.remove();

      setTimeout(function () {
        window.URL.revokeObjectURL(blobUrl);
      }, 1500);

      alert("Invoice PDF generated successfully.");
      try {
        window.close();
      } catch (closeErr) {
        console.warn("Window close blocked:", closeErr);
      }
      return;
    }

    const root = document.getElementById("invoiceTemplatePageRoot");
    if (!root) {
      alert("Invoice container not found.");
      return;
    }

    root.classList.add("itb-print-mode");
    setTimeout(function () {
      window.print();
      setTimeout(function () {
        root.classList.remove("itb-print-mode");
      }, 800);
    }, 300);
  } catch (err) {
    console.error("Print Error:", err);
    alert((err && err.message) ? err.message : "Unable to save invoice PDF.");
  }
}
