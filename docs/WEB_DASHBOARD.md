# Vercel dashboard shell

The web folder is a static Vercel-hostable dashboard shell. It embeds the existing ZonClip Studio served by the local Windows app. Video files, transcription models, renders, OAuth tokens, and posting queues stay on the PC; Vercel hosts only the small HTML page.

This is **same-PC browser access**, not remote access from a phone or another computer. A browser opened on another device would resolve 127.0.0.1 to that other device, not to your Windows PC.

## Deploy to Vercel

1. Push the latest ZonClip main branch to GitHub.
2. In Vercel, create a new project and import Tahir1907/ZonClip.
3. Set the project's **Root Directory** to web.
4. Select the **Other** framework preset. There is no build command and no environment variable or server secret required for this static shell.
5. Deploy and copy the exact production origin, for example https://your-project.vercel.app. Do not include a path or trailing slash.

## Allow that exact origin in the local Studio

1. On the Windows PC, open %USERPROFILE%\clipforge\config.json. Create the file if it does not exist, or add the field without removing your other settings.
2. Add the exact Vercel origin:

```json
{
  "web_origin": "https://your-project.vercel.app"
}
```

3. Restart ZonClip Studio so it loads the new setting.
4. Keep ZonClip running on the same PC and open your Vercel URL in a browser on that PC.
5. The web shell should display the local Studio inside its frame. If it does not, use Open locally and confirm that http://127.0.0.1:8765 loads on the same computer.

## Security model and boundaries

- The local Python server continues to bind to 127.0.0.1 only; it is not exposed as a public internet service.
- The local server accepts a remote parent origin only when it exactly matches web_origin.
- The local Studio HTML sends a Content-Security-Policy frame-ancestors directive allowing only itself and that exact configured origin.
- The Vercel shell does not receive OAuth tokens, account credentials, video bytes, or processing jobs.
- Do not put OAuth credentials or local tokens in Vercel environment variables. They remain in the local posting store on the PC.
- Do not use a wildcard origin such as *.vercel.app. Set one exact production origin.
- If you change the Vercel project domain, update web_origin and restart ZonClip.

This first hosted shell is intentionally simple. It does not provide cloud storage, cross-device remote control, cloud rendering, or account synchronization. Long video processing remains local by design.
