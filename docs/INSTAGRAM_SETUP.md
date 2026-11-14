# Instagram Reels publishing

ZonClip's Instagram integration uses Meta's official Instagram Platform with Facebook Login and the resumable Reels upload flow. It uploads the finished local MP4 directly to Meta's upload endpoint; it does not require making the clip publicly accessible on a third-party file host.

## Eligibility and Meta setup

This path requires all of the following:

- An eligible Instagram **Business or Creator** account.
- The Instagram professional account linked to a Facebook Page.
- A Meta developer app configured for Facebook Login for Business and the Instagram API.
- The requested permissions: instagram_basic, instagram_content_publish, pages_show_list, and pages_read_engagement.
- The app user's role/test access or Meta-granted access for those permissions.
- Any required Page Publishing Authorization and app/business review.

Meta's development-mode permissions may only work for app roles and testers. To publish for accounts outside those roles, complete Meta's required review and access process. ZonClip does not bypass platform review or account eligibility.

## Configure ZonClip

1. Open the Studio and choose **Posting**.
2. In the Instagram card, copy the displayed Redirect URI.
3. In the Meta developer app, add that exact URI to the valid OAuth redirect URI list.
4. Configure the Instagram API and Facebook Login for Business products, then request the permissions above.
5. Enter the Meta App ID and App Secret in ZonClip and save.
6. Choose **Connect Instagram** and approve the requested access in the browser.
7. ZonClip looks for a single Facebook Page with a linked Instagram professional account. If multiple linked accounts are granted, remove unused Page access from the app and connect again so the intended account is unambiguous.

The Page access token and account identifiers are stored locally in the existing posting.json file under the ZonClip local data folder. Never share that file or your App Secret.

## Publishing behavior

1. ZonClip checks the account's current Content Publishing Limit endpoint.
2. It creates a Reels media container with upload_type=resumable.
3. It streams the finished MP4 bytes to Meta's rupload endpoint.
4. It polls the container until Meta reports FINISHED, ERROR, or EXPIRED.
5. Only after FINISHED does it call media_publish.
6. It reads back the published media permalink and records the successful upload in the local queue.

The queue creates the Instagram container only when a scheduled item becomes due. This avoids creating containers that could expire before the scheduled time. Meta's API, not ZonClip, decides whether a container is publishable.

## Media and account notes

- The existing ZonClip vertical renderer outputs MP4; confirm the export meets Meta's current Reels video requirements before publishing.
- Instagram's API is for professional accounts, not personal consumer accounts.
- Meta can reject content, revoke tokens, require extra permissions, or change API limits. The queue surfaces those errors rather than marking an unsuccessful upload as posted.
- If a token expires, reconnect the account from the Posting panel.
- An app in development mode may successfully connect the developer's test account but still be unable to publish for other accounts until review is complete.

## Official references

- Instagram Platform: https://developers.facebook.com/docs/instagram-platform/
- Content Publishing: https://developers.facebook.com/docs/instagram-platform/content-publishing/
- Instagram API with Facebook Login: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/
- Graph API rate limits: https://developers.facebook.com/docs/graph-api/overview/rate-limiting/
