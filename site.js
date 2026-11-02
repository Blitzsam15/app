(() => {
  const root = document.querySelector("#storefront");
  if (!root) return;

  const tabs = [...document.querySelectorAll(".side-tab")];
  const categoryButtons = [...document.querySelectorAll(".category-button")];
  const categoryGroups = [...document.querySelectorAll(".product-category-group")];
  const categoryEmpty = document.querySelector(".category-empty");
  const panels = [...document.querySelectorAll(".shop-panel")];
  const modal = document.querySelector("#order-modal");
  const paymentModal = document.querySelector("#payment-modal");
  const orderForm = document.querySelector("#order-form");
  const quantityInput = orderForm.elements.quantity;
  let selectedPrice = 0;

  function setSide(side) {
    root.dataset.side = side;
    document.body.dataset.side = side;
    tabs.forEach((tab) => {
      const active = tab.dataset.side === side;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-pressed", String(active));
    });
    panels.forEach((panel) => {
      panel.hidden = panel.dataset.panel !== side;
    });
    try {
      localStorage.setItem("aunty-bunmi-side", side);
    } catch (_error) {
      // The shop still works when browser storage is unavailable.
    }
  }

  try {
    const savedSide = localStorage.getItem("aunty-bunmi-side");
    if (savedSide === "eggs" || savedSide === "kids") setSide(savedSide);
  } catch (_error) {
    // Keep the default eggs side when browser storage is unavailable.
  }
  tabs.forEach((tab) => tab.addEventListener("click", () => setSide(tab.dataset.side)));

  categoryButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const category = button.dataset.categoryFilter;
      let visibleGroups = 0;
      categoryButtons.forEach((categoryButton) => {
        const active = categoryButton === button;
        categoryButton.classList.toggle("active", active);
        categoryButton.setAttribute("aria-pressed", String(active));
      });
      categoryGroups.forEach((group) => {
        const visible = category === "All" || group.dataset.categoryGroup === category;
        group.hidden = !visible;
        if (visible) visibleGroups += 1;
      });
      if (categoryEmpty) categoryEmpty.hidden = category === "All" || visibleGroups > 0;
    });
  });

  function formatNaira(amount) {
    return `₦${Number(amount).toLocaleString("en-NG")}`;
  }

  function updateTotal() {
    const quantity = Number.parseInt(quantityInput.value, 10) || 0;
    document.querySelector("#order-total").textContent = formatNaira(selectedPrice * quantity);
  }

  function closeModal(backdrop) {
    backdrop.classList.remove("open");
    backdrop.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
  }

  document.querySelectorAll(".buy-button").forEach((button) => {
    button.addEventListener("click", () => {
      selectedPrice = Number(button.dataset.price);
      orderForm.elements.item_type.value = button.dataset.itemType;
      orderForm.elements.product_id.value = button.dataset.productId || "";
      document.querySelector("#selected-name").textContent = button.dataset.itemName;
      document.querySelector("#selected-price").textContent = formatNaira(selectedPrice);
      document.querySelector("#selected-unit").textContent = button.dataset.itemType === "eggs" ? "Per tray" : "One item";
      document.querySelector("#selected-icon").textContent = button.dataset.itemType === "eggs" ? "🥚" : "🎁";
      quantityInput.value = 1;
      document.querySelector("#order-error").textContent = "";
      updateTotal();
      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");
      document.body.classList.add("modal-open");
      orderForm.elements.buyer_name.focus();
    });
  });

  quantityInput.addEventListener("input", updateTotal);
  document.querySelectorAll(".modal-close").forEach((button) => {
    button.addEventListener("click", () => {
      closeModal(button.closest(".modal-backdrop"));
    });
  });
  [modal, paymentModal].forEach((backdrop) => {
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) closeModal(backdrop);
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeModal(modal);
      closeModal(paymentModal);
    }
  });

  orderForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = orderForm.querySelector('[type="submit"]');
    submitButton.disabled = true;
    document.querySelector("#order-error").textContent = "";
    try {
      const response = await fetch("/orders", {
        method: "POST",
        headers: {"X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content},
        body: new FormData(orderForm),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "We couldn't create that order.");
      document.querySelector("#payment-order-id").textContent = `#${result.order_id}`;
      document.querySelector("#bank-name").textContent = result.bank_name;
      document.querySelector("#account-number").textContent = result.account_number;
      document.querySelector("#account-name").textContent = result.account_name;
      document.querySelector("#payment-total").textContent = formatNaira(result.total);
      document.querySelector("#receipt-form").action = `/orders/${result.order_id}/receipt`;
      const whatsappLink = document.querySelector("#whatsapp-link");
      if (result.whatsapp) {
        const message = `Hello, I placed order #${result.order_id} at Aunty Bunmi's Shop.%0AName: ${encodeURIComponent(result.buyer_name)}%0AItem: ${encodeURIComponent(`${result.quantity} x ${result.item_name}`)}%0ATotal: ${encodeURIComponent(formatNaira(result.total))}`;
        whatsappLink.href = `https://wa.me/${result.whatsapp}?text=${message}`;
        whatsappLink.hidden = false;
      } else {
        whatsappLink.hidden = true;
      }
      closeModal(modal);
      paymentModal.classList.add("open");
      paymentModal.setAttribute("aria-hidden", "false");
      document.body.classList.add("modal-open");
    } catch (error) {
      document.querySelector("#order-error").textContent = error.message;
    } finally {
      submitButton.disabled = false;
    }
  });
})();
