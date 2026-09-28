# JEV for Griptape Nodes

Nodes for asking [TypeSafe](https://typesafe.ai)'s JEV model questions about text, and routing your flow based on the answer.

JEV doesn't write text. You give it some text and a narrow question, and it returns a typed answer with a probability. That makes it good for decisions inside a flow: is this prompt safe, does this message ask for a refund, is this caption about a person.

## Nodes

### Ask Yes/No (Noul)

Asks a yes/no question about some text and sends the flow down the **Yes** or **No** branch.

| Parameter | What it does |
|---|---|
| **Context** | The text to ask about. Type it in or connect any text output. JSON works too. It also has an output, so you can pass the same text on to the next node. |
| **Question** | A yes/no question about the context, such as "Does the customer ask for a refund?" |
| **Yes means** / **No means** | Optional, in the collapsed **Define Yes and No** group. Short descriptions of what should count as yes and as no. Fill in either or both. |
| **Say Yes at or above** | How sure JEV must be before the flow takes the Yes branch. The default is 0.5. Raise it to say Yes only when JEV is very sure. Lower it to say Yes on weaker signals. |
| **Answer** | True or false. |
| **Probability** | JEV's probability that the answer is yes, from 0 to 1. A value near 0.5 means yes and no are about equally likely. |

The collapsed **Data Outputs** group works like the If/Else node. Connect something to **Data if Yes** and **Data if No**, and **Output** passes along whichever matches the answer. Everything connected to those two inputs runs before the question is asked, whichever way the answer goes. If one of them is expensive, like an image generation, put it after the branch instead.

The collapsed **Advanced** group has the model choice. `jev-latest` is the newest stable model and is the right choice for most flows.

## Tips for good questions

- Ask one narrow thing per question. "Does the message ask for a refund?" works better than "Is this a refund request that needs urgent attention?"
- Give JEV everything it needs in the Context. It only sees what you connect.
- Most questions don't need **Yes means** and **No means**. Use them when the line between yes and no is subtle. For "Has the customer contacted support before?", does mentioning it once in passing count? Say so in **Yes means**. Try a few real examples with and without them, and keep whichever works better.
- For structured context, use JSON and refer to fields with backticks, such as "Does `ticket.body` mention a duplicate charge?"
- To pick a value for **Say Yes at or above**, run a few real examples and look at the **Probability** output.

## Limits

- **Text only.** JEV can't read images, audio, or video. To ask about an image, describe it first with a node like **Describe Image**, then connect that description to **Context**.
- **English works best.** Other languages work but are less accurate.
- **64k tokens per request**, covering the context and the question.

## Installation

1. Clone this repository into your Griptape Nodes workspace:

   ```bash
   cd `gtn config show workspace_directory`
   git clone https://github.com/<your-org>/griptape-nodes-library-jev.git
   ```

2. In the Griptape Nodes editor, open **Settings > Libraries**, click **+ Add Library**, and enter the path to `griptape-nodes-library-jev/griptape-nodes-library.json` in your workspace. Then click **Refresh Libraries**.

3. Get an API key at [console.typesafe.ai/keys](https://console.typesafe.ai/keys), then add it as `TYPESAFE_API_KEY` in **Settings > API Keys & Secrets**.

The nodes appear under the **JEV** category.

## Troubleshooting

- **"TYPESAFE_API_KEY is not set"**: add the key in **Settings > API Keys & Secrets**, using exactly that name.
- **"TypeSafe rejected the API key"**: the key is wrong or has been revoked. Create a new one at [console.typesafe.ai/keys](https://console.typesafe.ai/keys).
- **An image won't connect to Context**: JEV reads text only. See **Limits** above.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). For local runs, copy `.env.example` to `.env` and fill in `TYPESAFE_API_KEY`.

## License

Apache License 2.0. See [LICENSE](LICENSE).
