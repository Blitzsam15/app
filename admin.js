(() => {
  const toastStack = document.querySelector("#receipt-toast-stack");
  const knownReceiptsElement = document.querySelector("#known-receipts");
  if (!toastStack || !knownReceiptsElement) return;

  const knownReceipts = new Set(JSON.parse(knownReceiptsElement.textContent));
  const notificationUrl = "/admin/receipt-notifications";
  const pollInterval = 10000;

  function showReceiptToast(receipt) {
    const toast = document.createElement("div");
    toast.className = "receipt-toast";
    toast.setAttribute("role", "status");

    const message = document.createElement("p");
    message.textContent = `New payment receipt received from ${receipt.buyer_name} (Order #${receipt.order_id}).`;

    const reviewLink = document.createElement("a");
    reviewLink.href = "#orders";
    reviewLink.textContent = "Review order";

    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "toast-close";
    closeButton.setAttribute("aria-label", "Dismiss notification");
    closeButton.textContent = "×";
    closeButton.addEventListener("click", () => toast.remove());

    toast.append(message, reviewLink, closeButton);
    toastStack.append(toast);
    window.setTimeout(() => toast.remove(), 10000);
  }

  async function checkForReceipts() {
    try {
      const response = await fetch(notificationUrl, {
        headers: {Accept: "application/json"},
        credentials: "same-origin",
        cache: "no-store",
      });
      if (response.redirected || !response.headers.get("content-type")?.includes("application/json")) {
        window.location.assign(response.url);
        return;
      }
      if (!response.ok) throw new Error(`Receipt check failed with status ${response.status}.`);

      const result = await response.json();
      for (const receipt of result.receipts) {
        if (knownReceipts.has(receipt.receipt_image)) continue;
        knownReceipts.add(receipt.receipt_image);
        showReceiptToast(receipt);
      }
    } catch (error) {
      console.error("Could not check for new payment receipts.", error);
    }
    window.setTimeout(checkForReceipts, pollInterval);
  }

  window.setTimeout(checkForReceipts, pollInterval);
})();
