# Guatemala flight scan

73,437 trip combinations across 90 US airports, 2026-09-17T20:13:39+00:00 UTC.

All-in = Google Flights fare + ESTIMATED checked bag. Rows marked `guess` use a pessimistic placeholder and need verifying.

| # | All-in | Match | Fare | Bag | Route | Dates | Days | Airlines | Stops | Bag est |
|--:|-------:|------:|-----:|----:|-------|-------|-----:|----------|------:|---------|
| 1 | **$411** | 15 cheapest-only | $351 | $60 | FLL<->GUA | 2027-01-14 -> 2027-02-07 | 24 | COPA | 1 | stale |
| 2 | **$421** | 15 cheapest-only | $361 | $60 | FLL<->GUA | 2027-01-14 -> 2027-02-04 | 21 | COPA | 1 | stale |
| 3 | **$429** | 15 cheapest-only | $369 | $60 | FLL<->GUA | 2027-01-14 -> 2027-02-08 | 25 | COPA | 1 | stale |
| 4 | **$473** | 15 cheapest-only | $413 | $60 | FLL<->GUA | 2027-01-14 -> 2027-02-06 | 23 | COPA | 1 | stale |
| 5 | **$473** | 0 cheapest-only | $413 | $60 | FLL<->GUA | 2027-01-14 -> 2027-02-03 | 20 | COPA | 1 | stale |
| 6 | **$473** | 30 cheapest-only | $373 | $100 | LGA<->GUA | 2026-11-14 -> 2026-12-09 | 25 | American | 2 | stale |
| 7 | **$474** | 30 cheapest-only | $374 | $100 | TPA<->GUA | 2026-11-23 -> 2026-12-14 | 21 | Avianca | 1 | stale |
| 8 | **$474** | 15 cheapest-only | $374 | $100 | TPA<->GUA | 2027-01-14 -> 2027-02-04 | 21 | Avianca | 1 | stale |
| 9 | **$478** | 30 cheapest-only | $383 | $95 | PVD->GUA / GUA->TPA | 2026-11-12 -> 2026-12-03 | 21 | American, United | 3 | stale |
| 10 | **$478** | 30 cheapest-only | $383 | $95 | PVD->GUA / GUA->JFK | 2026-11-12 -> 2026-12-09 | 27 | American, Delta | 3 | stale |
| 11 | **$484** | 25 cheapest-only | $390 | $94 | LAX->GUA / GUA->EWR | 2027-01-11 -> 2027-02-01 | 21 | JetBlue, United | 2 | stale |
| 12 | **$484** | 25 cheapest-only | $390 | $94 | LAX->GUA / GUA->EWR | 2027-01-11 -> 2027-02-04 | 24 | JetBlue, Delta | 2 | stale |
| 13 | **$484** | 15 cheapest-only | $424 | $60 | TPA<->GUA | 2027-01-11 -> 2027-02-01 | 21 | COPA | 1 | stale |
| 14 | **$484** | 0 cheapest-only | $424 | $60 | TPA<->GUA | 2027-01-14 -> 2027-02-02 | 19 | COPA | 1 | stale |
| 15 | **$485** | 25 cheapest-only | $386 | $99 | LAX->GUA / GUA->JFK | 2027-01-11 -> 2027-02-01 | 21 | JetBlue, American | 2 | stale |
| 16 | **$499** | 0 cheapest-only | $391 | $108 | ATL->GUA / GUA->EWR | 2027-01-09 -> 2027-01-28 | 19 | Frontier, United | 2 | stale |
| 17 | **$499** | 15 cheapest-only | $391 | $108 | ATL->GUA / GUA->EWR | 2027-01-09 -> 2027-02-04 | 26 | Frontier, Delta | 2 | stale |
| 18 | **$512** | 35 partial | $417 | $95 | TPA->GUA / GUA->EWR | 2026-12-05 -> 2026-12-24 | 19 | American, United | 3 | stale |
| 19 | **$512** | 35 partial | $417 | $95 | TPA->GUA / GUA->TPA | 2026-12-05 -> 2026-12-24 | 19 | American, United | 3 | stale |
| 20 | **$512** | 35 partial | $417 | $95 | TPA->GUA / GUA->BOS | 2026-12-05 -> 2026-12-24 | 19 | American, United | 3 | stale |
| 21 | **$512** | 35 partial | $417 | $95 | TPA->GUA / GUA->LGA | 2026-12-05 -> 2026-12-24 | 19 | American, Delta | 3 | stale |
| 22 | **$512** | 35 partial | $417 | $95 | TPA->GUA / GUA->JAX | 2026-12-05 -> 2026-12-24 | 19 | American, United | 3 | stale |
| 23 | **$634** | 50 partial | $574 | $60 | DEN<->GUA | 2027-01-11 -> 2027-02-01 | 21 | COPA | 1 | stale |
| 24 | **$675** | 65 close | $535 | $140 | DEN<->GUA | 2026-11-26 -> 2026-12-17 | 21 | Air Canada, Avianca | 2 | guess |
| 25 | **$677** | 50 partial | $587 | $90 | DEN<->GUA | 2027-01-12 -> 2027-02-02 | 21 | United | 1 | stale |
| 26 | **$779** | 100 ideal | $689 | $90 | DEN<->GUA | 2026-12-05 -> 2026-12-26 | 21 | United | 1 | stale |
