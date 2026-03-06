def build_step_prompt(step_title: str, step_content: str) -> str:
    return f"Step: {step_title}\n\nInstructions:\n{step_content}" 
