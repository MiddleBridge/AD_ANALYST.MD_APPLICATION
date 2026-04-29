# Known Limitations

This project is an application artifact and prototype workflow, not a production investment system.

## Not an autonomous investment decision system

The system supports screening and synthesis, but it does not make final investment decisions.

## Human review required

All recommendations, scores, and founder communications require human review.

## No automatic founder email sending

The system may draft responses, but it should not send emails automatically.

## External research depends on configured tools

External validation depends on available APIs, search tools, and data sources.

## Scoring quality depends on evidence quality

Scores are only useful when supported by evidence, confidence, and missing-data flags.

## Credentials required for integrations

Gmail, Notion, OpenAI, and other integrations require local credentials or API keys.

## Not production-hardened

More work would be needed before production use:
- stronger auth
- deployment hardening
- observability
- rate-limit handling
- better test coverage
- secret management
