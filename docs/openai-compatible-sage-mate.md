# Sage Mate OpenAI-compatible member API

Sage Mate is available to standard OpenAI clients at:

```text
https://api.sage.org.ai/v1
```

Use model `sage-mate`. Requests with this model pass through the full Sage Mate
workflow: member authentication, intent routing, knowledge retrieval, research
method guidance, answer validation, and provenance metadata. They do not call
the raw Qwen model directly.

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://api.sage.org.ai/v1",
    api_key="<lab-member-token>",
)

response = client.chat.completions.create(
    model="sage-mate",
    messages=[{"role": "user", "content": "请按七问法评价这个研究课题。"}],
    max_tokens=1024,
    extra_body={
        "sage_mate": {
            "deep_thinking": True,
            "skill_routing": True,
            "web_search": False,
        }
    },
)
print(response.choices[0].message.content)
```

The `sage_mate` options are optional. The endpoint accepts normal string
message content and OpenAI text content parts. Prior system/assistant messages
are passed as bounded conversation context; the final user message is the
current Sage Mate question.

The response follows the Chat Completions shape. It also includes a top-level
`sage_mate` object containing `conversation_id`, `workflow_action`,
`decision_mode`, visible `knowledge_hits`, `answer_basis`, and request timing.
`stream=true` is supported as validated-answer streaming: Sage Mate completes
its grounding checks first, then emits one final content chunk and `[DONE]`.

Member tokens are accepted only for model `sage-mate`. They cannot select the
raw served model or bypass Sage Mate policy. Server-side records contain only a
SHA-256 digest, an expiry, and revocation state; plaintext is returned only at
issuance.
