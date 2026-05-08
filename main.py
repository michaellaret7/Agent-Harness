from agent.agent import Agent


if __name__ == '__main__':
    agent = Agent(provider='anthropic', model='claude-opus-4-7')
    # agent.run('do a deep dive on the current codebase and propose a plan to make some improvements to the agent. You may not make any changes yet.')

    while True:
        prompt = input('> ')
        if prompt == 'exit':
            break
        print()
        agent.run(prompt)
        print()
