# Electrobun Secrets Startup

## Is

On startup, the Electrobun app tries to build its initial secrets state immediately.
That currently causes it to probe the configured secrets provider during app boot.
Some providers require user interaction before they can answer.
When that happens, startup treats the missing immediate response as a failure and the app boot path breaks.
Moving that work fully into startup would also risk repeated user prompts if the app keeps retrying or re-probing during normal navigation.

## Should Be

Startup should complete from local, non-interactive state only.
Secrets-provider-backed information should be fetched lazily when the user opens a view that actually needs it.
That lazy fetch should wait for the provider response instead of assuming it will return immediately.
The app should remember that a provider interaction is already in progress or has already completed, so the user is not asked to unlock or approve the same thing again and again.
