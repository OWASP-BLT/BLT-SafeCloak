# Contributing to BLT-SafeCloak

Thank you for your interest in contributing to BLT-SafeCloak! This guide will help you get started.

## Getting Started

1.  **Fork the repository** on GitHub.
2.  **Clone your fork** locally.
3.  **Setup Environment**:
    ```bash
    # Install Python development tools
    pip install -r requirements-dev.txt

    # Install Playwright browsers
    playwright install chromium --with-deps
    ```
4.  **Local Testing (after setup)**:
    ```bash
    # Download the PeerJS library to tests/vendor/
    mkdir -p tests/vendor && curl -fL https://unpkg.com/peerjs@1.5.2/dist/peerjs.min.js -o tests/vendor/peerjs.min.js
    
    # Run tests
    pytest tests/ -v
    ```

## Development Workflow

### Setup

```bash
# Install Python development tools
pip install -r requirements-dev.txt

# Install Playwright browsers
playwright install chromium --with-deps
```

### Running Locally

```bash
# Start development server
npx wrangler dev --no-reload
```

### Local Testing

To run the full E2E test suite locally:

1. **Vendor PeerJS**: Download the library to `tests/vendor/` to avoid external network calls:
   ```bash
   mkdir -p tests/vendor && curl -fL https://unpkg.com/peerjs@1.5.2/dist/peerjs.min.js -o tests/vendor/peerjs.min.js
   ```
2. **Run tests**:
   ```bash
   pytest tests/ -v
   ```

## Technical Standards

Access the application at `http://localhost:8787`.

### Code Style

- **Python**: Follow PEP 8 (yapf format).
- **JavaScript**: Functional approach, use Crypto Web API for encryption.
- **CSS**: Tailwind CSS for styling.
- Mobile-first responsive design
- BEM naming convention for CSS classes

### Project Structure

To add a new feature or page (e.g., `feature.html`):

1. **Create HTML**: Add your file to the `src/pages/` directory.
2. **Add JavaScript**: Place any corresponding logic in `public/js/`.
3. **Add Route**: Define the path in the `PAGES_MAP` dictionary in `src/main.py`:
   ```python
   PAGES_MAP = {
       '/': 'index.html',
       '/feature': 'feature.html',
   }
   ```
4. **Update Navigation**: Ensure all navigation bars in the HTML pages point to the new clean URL (e.g., `/feature`).

### Adding New Features

1. Discuss in GitHub issues first
2. Update documentation
3. Add necessary tests
4. Update README if user-facing

## Cloudflare Workers Resources

### Official Documentation

- [Cloudflare Workers Docs](https://developers.cloudflare.com/workers/)
- [Python Workers Guide](https://developers.cloudflare.com/workers/languages/python/)

Use helper functions from `src/libs/utils.py`:

```python
from libs.utils import html_response, json_response, cors_response

# Return HTML
return html_response("<h1>Hello</h1>")

# Return JSON
return json_response({"status": "success"})

# CORS preflight
return cors_response()

## Common Tasks

### Adding a New Route

```python
# In src/main.py
if path == '/new-page':
    html_content = Path(__file__).parent / 'pages' / 'new-page.html'
    return Response.new(html_content.read_text(), {
        'headers': {'Content-Type': 'text/html'}
    })
```

### Debugging

```bash
# View logs in real-time
npx wrangler tail
```