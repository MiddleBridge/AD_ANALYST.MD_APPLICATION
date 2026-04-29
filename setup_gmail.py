"""
Gmail OAuth Setup Helper

Run once: python setup_gmail.py
This opens a browser for Google OAuth consent and saves token.json.

Prerequisites:
1. Go to https://console.cloud.google.com/
2. Create a project (or use existing)
3. Enable "Gmail API" in APIs & Services
4. Create OAuth 2.0 credentials (Desktop App type)
5. Download credentials JSON → save as credentials.json in this directory
"""
import json
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

console = Console()


def run_setup():
    console.print(Panel.fit(
        "[bold cyan]Example VC Fund Screening Agent — Gmail Setup[/bold cyan]\n\n"
        "This wizard authenticates your Gmail account.",
        border_style="cyan",
    ))
    console.print()

    credentials_path = "credentials.json"
    if not Path(credentials_path).exists():
        console.print("[red]credentials.json not found![/red]")
        console.print()
        console.print("Steps to get it:")
        console.print("  1. Go to [link]https://console.cloud.google.com/[/link]")
        console.print("  2. Create or select a project")
        console.print("  3. Enable Gmail API: APIs & Services → Library → Gmail API → Enable")
        console.print("  4. Create credentials: APIs & Services → Credentials")
        console.print("     → Create Credentials → OAuth client ID → Desktop App")
        console.print("  5. Download JSON → rename to credentials.json → place here")
        console.print()
        return

    console.print("[green]credentials.json found[/green]")
    console.print("Opening browser for OAuth consent...")
    console.print()

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        SCOPES = [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.labels",
        ]
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
        creds = flow.run_local_server(port=0)
        Path("token.json").write_text(creds.to_json())
        console.print("[green]✓ OAuth successful! token.json saved.[/green]")
        console.print()

        env_path = Path(".env")
        if not env_path.exists():
            user_email = console.input("Enter your Gmail address: ").strip()
            env_path.write_text(
                f"ANTHROPIC_API_KEY=\n"
                f"GMAIL_CREDENTIALS_PATH=credentials.json\n"
                f"GMAIL_TOKEN_PATH=token.json\n"
                f"GMAIL_PROCESSED_LABEL=Fund/Screened\n"
                f"GMAIL_USER_EMAIL={user_email}\n"
                f"REVIEWER_EMAIL={user_email}\n"
                f"REVIEWER_NAME=Analyst\n"
                f"CALENDLY_LINK=\n"
                f"POLLING_INTERVAL_MINUTES=15\n"
                f"GATE2_PASS_THRESHOLD=6.0\n"
            )
            console.print("[green]✓ .env created[/green]")
            console.print("[yellow]→ Add your ANTHROPIC_API_KEY to .env[/yellow]")
        else:
            console.print("[dim].env already exists — update ANTHROPIC_API_KEY if needed[/dim]")

        console.print()
        console.print("[bold green]Setup complete![/bold green]")
        console.print("Run: [cyan]python main.py --test your_deck.pdf[/cyan] to test the pipeline")
        console.print("Run: [cyan]python main.py --once[/cyan] to process current Gmail inbox")
        console.print("Run: [cyan]python main.py[/cyan] for continuous monitoring")

    except Exception as e:
        console.print(f"[red]Setup failed: {e}[/red]")


if __name__ == "__main__":
    run_setup()
