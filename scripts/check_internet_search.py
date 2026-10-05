from langchain_core.messages import HumanMessage

from rag_agent.graph import graph_builder


def test():
    app = graph_builder.compile()
    question = "Mức lương cơ sở mới nhất năm nay là bao nhiêu?"
    print(f"Question: {question}")
    
    # Run the graph
    inputs = {"messages": [HumanMessage(content=question)]}
    
    for event in app.stream(inputs, {"configurable": {"thread_id": "test_thread_unique_4"}}):
        print("NODE FIRED:", event.keys())
        if 'agent_tools' in event:
            print("TOOL MESSAGES:", event['agent_tools']['agent_messages'])

if __name__ == "__main__":
    test()
