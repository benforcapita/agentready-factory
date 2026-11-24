"""Passive collection limits and ordered probe identities."""

IDENTITIES = (
    ("browser", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    ("Googlebot", "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"),
    ("GPTBot", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.3; +https://openai.com/gptbot)"),
    ("ChatGPT-User", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ChatGPT-User/1.0; +https://openai.com/bot)"),
    ("OAI-SearchBot", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)"),
    ("ClaudeBot", "ClaudeBot/1.0; +https://anthropic.com"),
    ("Claude-User", "Claude-User/1.0; +https://anthropic.com"),
    ("PerplexityBot", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)"),
    ("Google-Extended", "Mozilla/5.0 (compatible; Google-Extended; +https://developers.google.com/search/docs/crawling-indexing/overview-google-crawlers)"),
)
PATHS = ("/", "/robots.txt", "/llms.txt", "/sitemap.xml")
DISCOVERY_PATHS = (
    "/.well-known/ai-catalog.json", "/llms-full.txt",
    "/.well-known/security.txt", "/sitemap_index.xml", "/.well-known/ai-plugin.json",
    "/poppy.json", "/.well-known/poppy.json", "/.well-known/mcp.json",
)
TIMEOUT = 10
MAX_REQUESTS = 100
FAKE_IP_POLICY = "refuse"
DOH_ENDPOINT = "https://cloudflare-dns.com/dns-query"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
