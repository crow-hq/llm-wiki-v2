# Incident report: web shop outage, 14 July 2026

Prepared by: Davide Gallo
Reviewed by: Sara Conti, Giulia Ferrero
Status: closed 14 July, 17:40

## Summary

The web shop was unavailable for customers from 10:52 to 12:37 (1 h 45 min). Checkout returned an error page and the subscription page did not load. Orders placed before 10:52 went through; some orders placed from 10:40 were charged twice on the card but only one order was created. Refunds for the double charges were issued the same afternoon.

## Timeline (all times CEST)

- 10:31 Chiara posts the cold brew "back on Thursday" story on Instagram. Traffic about 3x normal over the next 20 minutes, mostly on the cold brew product page.
- 10:44 Shop response times go above 8 seconds on checkout. Davide sees this in the uptime tool (an automated alert, not a human).
- 10:52 Checkout returns 503. Subscription and account pages fail too.
- 10:58 Davide calls the hosting provider's support line. Ticket opened, priority "urgent" because the account is a paid plan.
- 11:20 Hosting says the database connection pool was full. Restarting the app server does not help.
- 11:35 Sara sends a message to customers on Instagram saying the shop is down for maintenance (this was the wrong wording; the outage was not planned. Sorry).
- 12:02 Hosting adds a second database replica and raises the pool limit. Checkout starts returning OK at 12:10 but is slow.
- 12:37 Shop back to normal response times. Davide runs test orders (three) and cancels them.
- 13:15 Elena starts checking payments against orders. Finds 23 double charges (card authorised twice after a timeout) on orders placed 10:40 to 10:52.
- 15:30 Refunds issued for the 23 double charges via Stripe. Refund confirmation emails sent automatically; Davide also wrote to the 23 people to explain.

## Cause

The shop's checkout calls the payment provider and writes the order in one transaction. When the payment provider answered slowly (around 9 seconds at 10:40 because of an issue on their side, confirmed later by their status page), the transaction held the database connection open. With the Instagram traffic on top, the pool filled and the shop could not open new connections.

The double charges happened because the browser retried the payment after the timeout, and the first authorisation went through but no order was saved.

## What went wrong

- We had no alert on database connections, only on response time.
- The outage message on Instagram said "maintenance". It should have said "technical problem". We have replaced it with a note on the shop front page.
- Nobody was looking at the shop when the Instagram post went out. Chiara did not know the cold brew page would get that traffic.

## Actions

1. Alert on database connections above 80% of pool. Davide with the hosting provider, by 24 July.
2. Payment retries: checkout to show "payment pending, do not press again" after 5 seconds. Dev contractor (Simone, two days). By 31 July.
3. Chiara to tell the ops channel before any post that is likely to send traffic to the shop. Agreed.
4. Elena to check Stripe weekly for authorisations without orders (report from her side, not from the shop).
5. Hosting to say in writing whether the plan includes a second replica by default. Giulia wants to know the monthly cost difference before deciding.

## Customer impact

- 23 double charges, all refunded 14 July. Refund takes 3 to 5 working days on the card.
- About 140 abandoned checkouts (estimate from the analytics; not everyone who left would have bought).
- 4 customer emails about the outage, all answered by 15 July.
- The cold brew page had 910 views during the outage, which we cannot convert.
