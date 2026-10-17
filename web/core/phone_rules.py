"""What a phone or an iPad may do with each laptop page (owner, 2026-10-17; mock-up docs/mockups/ai_phone_limits.html). Everyone alike, the System
admin too. A laptop with a narrow window is still a laptop. The phone pages (/phone/...) and approving a sign-in are not laptop pages and are not
touched here.

  "laptop" -- done on the laptop only: left out of the phone's menu and Home; opened from a link, the page says so.
  "look"   -- looked at on the phone: every form is left out and any change refused, with a word on where it is done.
  (not listed) -- as on the laptop.

The phone is told apart by what its browser says (an iPad by its touch screen, core.views._ua): it stops slips and a found phone; it is not a lock --
each person's access and the phone approval remain the protection."""

PHONE = {
    "settings": "laptop", "booklets": "laptop", "si-booklets": "laptop", "creditors": "laptop",
    "price-lists": "look", "order-statements": "look", "purchase-orders": "look", "bank-cash": "look", "lenders": "look", "bank-loans": "look",
    "credit-cards": "look", "stock-counts": "look", "reports": "look", "analytics": "look", "income-statement": "look",
}

# where the job is done on the phone instead, when there is a phone page for it
PHONE_INSTEAD = {"purchase-orders": ("/phone/in/", "Goods came? Stock in"), "stock-counts": ("/phone/count/", "Count on the phone: Stock count"),
                 "price-lists": ("/phone/prices/", "Looking up a price? Price list")}

LAPTOP_WHY = {"settings": "People, access, switches and sign-in can change who may do what, so they are kept off phones and iPads.",
              "booklets": "A check number series set wrong affects every check after it.",
              "si-booklets": "An invoice number series set wrong affects every invoice after it.",
              "creditors": "A supplier's name and terms feed every payable."}


def kind(page_id: str) -> str | None:
    return PHONE.get(page_id)
