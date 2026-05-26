# Payments layer

**What it does.** Price a service, pay, verify a payment, refund.

## Interface

```python
class Payments(Protocol):
    async def quote(self, service: ServiceRef) -> Quote: ...
    async def pay(self, to: AgentId, amount: Money, ref: PaymentRef) -> Receipt: ...
    async def verify_payment(self, ref: PaymentRef) -> PaymentStatus: ...
    async def refund(self, ref: PaymentRef) -> None: ...
```

Full definition: [`nest_core/layers/payments.py`](../../packages/nest-core/nest_core/layers/payments.py).

## Default plugin

`prepaid_credits` — in-memory debit/credit ledger. Constant-price
quotes, raises on insufficient balance, supports refund by `PaymentRef`.

Source: [`nest_plugins_reference/payments/prepaid_credits.py`](../../packages/nest-plugins-reference/nest_plugins_reference/payments/prepaid_credits.py).

## Bundled alternative: `htlc_escrow`

`htlc_escrow` — hash- and time-locked conditional payments. Funds are
*escrowed* at `pay()` time and released only when the payee reveals a
preimage matching the payer's hashlock. If the timelock expires before
a claim, the payer can call `refund_expired` to reclaim the funds
unilaterally.

Use it when you want to stress-test counterparty-trust-minimized flows
(atomic swaps, conditional delivery, marketplace escrow, payment-on-
proof-of-delivery). Drop-in compatible with the base `Payments`
interface — protocols that don't know HTLC just see prepaid-style
direct transfers.

Source: [`nest_plugins_reference/payments/htlc_escrow.py`](../../packages/nest-plugins-reference/nest_plugins_reference/payments/htlc_escrow.py).

```yaml
layers:
  payments: htlc_escrow
```

```python
secret, lock = HtlcEscrow.make_secret(b"order-1")
await payments.pay(
    seller, Money(amount=50), PaymentRef("p1"),
    hashlock=lock, timelock_ticks=200,
)
# seller delivers → buyer reveals secret → seller claims
await payments.claim(PaymentRef("p1"), secret)
# OR: seller never delivers → buyer waits out timelock → refunds
payments.advance_clock(201)
await payments.refund_expired(PaymentRef("p1"))
```

## Writing your own

See [`writing-a-plugin.md`](../writing-a-plugin.md) — the full
walkthrough on that page builds a custom payments plugin end-to-end.
Register under entry point group `nest.plugins.payments`.

Good fits to test here: escrow, streaming payments, multi-party
settlement, on-chain stubs, x402-style HTTP payments.
