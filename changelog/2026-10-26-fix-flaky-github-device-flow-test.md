# Fix a flaky GitHub device-flow test that failed CI

### Fixed
- The frontend test "runs the device flow: shows the code, polls, then shows the connected account" failed on CI and on a second machine. Its mock approved the sign-in on the second poll with a zero-second interval, so the screen could switch to "Connected" before the test had checked the code card and the `github.com/login/device` link. The mock now stays "pending" until the test has checked the card and seen at least two polls, then approves. The app code is unchanged; this was a race in the test, not a bug in the product.

### Upgrade notes
- None. Test only; no user-visible change, so `docs/features.md` needs no edit.
