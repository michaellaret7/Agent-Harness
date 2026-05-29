from agent.agent import Agent
from coding.tools.bash import bash
from coding.tools.edit import edit
from coding.tools.glob import glob
from coding.tools.grep import grep
from coding.tools.tree import tree
from coding.tools.write import write
# from agent.sinks import StdoutSink
from dotenv import load_dotenv

load_dotenv()

agent = Agent(
    provider='openrouter',
    model='anthropic/claude-opus-4.8',
    system="You are a helpful assistant that can help with coding tasks. Also do you have any skills?",
    task="Write a simple hello world program in Python and create a haiku about the weather.",
)

agent.run()