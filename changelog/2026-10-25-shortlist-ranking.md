# Better suspect shortlist for investigations

### Changed
- The investigation's shortlist now keeps one suspect per file, counts a commit that shares no words with the report for much less, and ranks docs, tests and changelogs lower unless a stack trace names them. On the real-bug eval (this project's history, 10 cases) the true file is in the top 1 for 40% of cases (was 20%), top 3 for 50% (was 40%) and top 5 for 60% (was 40%). Tuned and scored on the same ten cases, so treat it as optimistic.
