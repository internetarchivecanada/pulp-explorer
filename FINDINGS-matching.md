# Pulp Explorer — automatic matching results

Date: 2026-10-06. Code: `pipeline/match.py`. Hand-checks: `eval/sample_match.csv`, `eval/match_adjudicated.json`.

## Summary

- **3,947 more scans matched to ISFDB issues, adding 2,560 issues** that no ISFDB editor had linked to a scan. The site now covers **5,313 issues** (up from 2,753), 91.7k works and 109.5k exact page links (figures after the 2026-10-07 fix below).
- **Precision:**
  - **Against ISFDB editors' own links:** 2,076 automatic matches, and all agreed with the scans. Seven disagreements were settled by viewing covers and contents pages. In 3 the matcher was right and ISFDB's link was wrong; in 1 ISFDB has two records for the same issue; 3 exposed matcher bugs, which are now fixed. Caveat: the rules were tuned on this set.
  - **On new matches (held out):** 50 of 50 checked against page images were correct, with 25 strong and 25 weak matches sampled. 95% lower bound: about 93%.
  - **Page-count sanity check:** 3,821 of 3,933 matched scans have about the issue's printed page count. 98 are smaller (pages missing from the scan); none is larger.
- **Recall on known links:** 75%. Unmatched scans are left out rather than guessed.

## How it works

1. **Candidates.** Each scan's cover date (month, season, exact day, volume/number) and magazine name are parsed from its identifier and title. Name aliases are learned from ISFDB's existing links, for example `Astounding_…` belongs to the Astounding/Analog series. Neighbouring months are tried only when no issue has the exact date.
2. **Verification.** Each candidate issue's distinctive titles are searched for in the scan's OCR. Departments that repeat across issues ("Letters") are skipped, as are serial installments that repeat in neighbouring issues. Candidates with clearly fewer printed pages than the scan are dropped, which rules out partial British reprint editions. The candidate that finds the most titles wins, but only with at least half of its titles found and a clear lead.
3. **Publishing.** Matches are labelled on the site as automatic, with the evidence shown ("4 of 4 stories found in the scan").

## Mistakes found and fixed along the way

- **UK reprint editions.** Atlas's British Astounding has the same title and date as the US issue but a fraction of the stories. Fixed with the page-count filter.
- **Neighbouring issues** shared serial installments and "next issue" previews. Fixed by dropping serials from the test and requiring the exact date when it's available.
- **Weeklies and numbered issues** (Scoops, Fantasy Book). Fixed by comparing the full date and the volume/number.

## Possible errors in ISFDB (worth reporting to ISFDB)

| scan | ISFDB links it to | the scan shows |
|---|---|---|
| `Amazing_Stories_Quarterly_v02n04_1929-Fall_Missing_ibcbc` | Summer 1929 | Fall 1929 |
| `Donald_A._Wollheim_ed._Avon_Fantasy_Reader_no._11_1949` | No. 12 | No. 11 |
| `amazing-stories-v-25n-03-1951-03` | November 1951 | March 1951 |

## Update 2026-10-07: a candidate bug, and the "not indexed" listing

- **The bug.** Identifiers with words and digits run together (`AdventureV042N02…`, `LunaMonthly09197002`) produced an empty name key. All such items then shared one alias list, which gave them spurious candidate magazines.
- **The fix.** Words are now split out of run-together identifiers, and empty keys are never learned or used.
- **What it corrected:** 2 of the 3,933 published matches were wrong, and neither had been caught by the 50-sample check.
  - `astounding-v-005-n-01-1931-01` had been matched to February 1931 (2 of 3 stories found). It's now January 1931 (8 of 8 found), which agrees with its title.
  - `LunaMonthly09197002`, a news fanzine, had been matched to Analog for February 1970, most likely because it reviews that issue. It's now unmatched.
- **Current totals:**
  - 3,947 matches covering 2,560 new issues.
  - On the known links: 2,079 accepted, all correct, recall 75%.
- **"Also scanned, not indexed" listing.**
  - 1,913 unconfirmed scans are listed on their magazine's page, under the uploader's own title and grouped by the year in that title.
  - A scan is listed only when every candidate issue belongs to that one magazine.
  - The page explains that these scans have no contents or story links, how they were filed, and that a few may be misfiled. Each scan shows its reason: "issue not confirmed" or "no readable text".
  - The listing adds 93 magazines that have no indexed issue yet.

## Not done

- **Unconfirmed scans.** 2,136 scans had a candidate issue but weren't confirmed (1,981 rejected, 155 with no text), and about 19.5k had no candidate at all, mostly because they aren't in ISFDB. One idea is open: an AI pass (local model) that proposes issues for the rejected scans, with the same content check still required before anything is indexed.
