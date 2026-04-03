# BLT-SafeCloak

Privacy-focused peer-to-peer communication platform built on Cloudflare Workers. Provides secure video chat, voice communication, AI-powered notes, and explicit consent management.

## Features

- **P2P Video/Voice Chat**: WebRTC-based communication with end-to-end encryption
- **Consent Management**: Built-in consent tracking and verification system
- **Secure Notes**: AI-powered note-taking with client-side encryption
- **Edge Computing**: Deployed on Cloudflare's global network for low latency
- **Zero-Knowledge Architecture**: Server never accesses unencrypted content

## Architecture

- **Backend**: Python Workers on Cloudflare Edge
- **Frontend**: Vanilla JavaScript with WebRTC
- **Deployment**: Cloudflare Workers with asset hosting
- **Encryption**: Client-side cryptography for all sensitive data

## Requirements

- Python >= 3.11
- Cloudflare account (for deployment)

## Installation

```bash
# Install Python development tools
pip install -r requirements-dev.txt

# Install Playwright browsers (for E2E tests)
playwright install chromium --with-deps
```

## Development

```bash
# Start local development server
npx wrangler dev --no-reload

# Format Python code
yapf -i -r src/

# Check code quality (formatting + type checking)
yapf -d -r src/ && mypy src/

# Type checking only
mypy src/

# Check formatting without modifying
yapf -d -r src/
```

The development server runs on `http://localhost:8787` with hot reload enabled.

### Code Formatting

- **Python**: yapf (PEP 8 style, 100 char line limit)
- **HTML/CSS/JS**: CDN-based (no local build required)

## Deployment

Deploy to Cloudflare Workers:

```bash
npx wrangler deploy
```

### Project Structure

```
src/
  main.py           # Main application entry point
  libs/
    utils.py        # Utility functions (html_response, json_response, etc.)
  pages/            # HTML pages
    index.html      # Landing page
    video-chat.html # Video chat interface
    notes.html      # Notes interface
    consent.html    # Consent management
public/
  css/              # Stylesheets
  js/               # Client-side JavaScript
    crypto.js       # Cryptography utilities
    video.js        # WebRTC implementation
    notes.js        # Notes functionality
    consent.js      # Consent logic
    ui.js           # UI components and utilities
pyproject.toml      # Python project configuration
requirements.txt    # Production dependencies
requirements-dev.txt # Development dependencies
wrangler.toml       # Cloudflare Worker configuration
```

### URL Structure

All pages use clean URLs without `.html` extensions:

- `/` - Home page
- `/video-chat` - Video chat interface
- `/notes` - Secure notes
- `/consent` - Consent management

## Security

- All sensitive data is encrypted client-side before transmission
- Server acts as a signaling relay only
- No persistent storage of communication content
- Consent verification required before session establishment

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines and resources.

## License

MIT License - see [LICENSE](LICENSE) for details.

## OWASP

This project is part of the OWASP Bug Logging Tool (BLT) initiative.
