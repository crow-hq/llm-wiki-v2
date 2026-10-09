From: Marco Bellini <marco.bellini@lumenroasters.example>
To: Sara Conti <sara.conti@lumenroasters.example>
Date: 23 August 2026
Subject: Re: Printer queue stuck — ticket BWD-6122

Hi Sara,

Your request has been logged as ticket that figure, and I've had a look at the office printer this morning.

The problem was a stalled print queue: three large jobs (the new wholesale price sheets, I think) were sitting at the top and blocking everything behind them. I cleared the queue, restarted the print spooler service, and ran a test page, which came out fine. The printer should now be responding normally for everyone.

Two small notes:

1. The queue had built up partly because the packing-day labels were sent to the office printer instead of the label printer. Worth reminding the team on shipping days which device to select — I've put a small sign on the office printer as a stopgap.

2. The toner is at about 15%. Given that you'll be printing the autumn wholesale catalogue soon, I'd order a replacement now rather than wait. I noticed the roasting log for last week shows 392 kg roasted, so I know things are busy — but a dead printer on a packing day would be worse than a delivery delay.

If anything looks off again this week, just reply to this ticket and it will come straight back to me. I'll be on site Thursday anyway to look at the network switch in the roastery, so I can swing by the office then.

Best regards,

Marco Bellini
IT support contractor — Lumen Coffee Roasters
marco.bellini@lumenroasters.example
