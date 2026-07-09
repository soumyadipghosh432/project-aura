import os
from llama_cpp import Llama
from app.config import OFFLINE_MODEL_HOME, active_model, fallback_model
from app.telemetry import telemetry_span

class ModelManager:
    def __init__(self):
        self.llm = None
        self.active_model_name = active_model["name"]
        self.fallback_model_name = fallback_model["name"]
        self.is_fallback_active = False

    def load_model(self):
        """Loads the active LLM. If OOM or any exception occurs, 
        logs a warning and silently falls back to the Phi fallback model.
        """
        model_path = os.path.join(OFFLINE_MODEL_HOME, self.active_model_name)
        print(f"Loading primary active model: {self.active_model_name} from {model_path}...")

        with telemetry_span("llm_initialization") as span:
            try:
                # Initialize Llama model on CPU.
                # n_ctx=2048 provides sufficient context headroom for RAG and categorization tasks
                self.llm = Llama(
                    model_path=model_path,
                    n_ctx=2048,
                    verbose=False
                )
                span.message = f"Successfully initialized primary active model: {self.active_model_name}"
                print(span.message)
            except Exception as e:
                # Log exception type and message to the telemetry logs table
                error_msg = f"Failed to initialize primary model '{self.active_model_name}': {str(e)}."
                print(f"Warning: {error_msg} Invoking silent fallback mechanism...")
                span.message = f"{error_msg} Silently falling back to {self.fallback_model_name}."
                
                fallback_path = os.path.join(OFFLINE_MODEL_HOME, self.fallback_model_name)
                try:
                    self.llm = Llama(
                        model_path=fallback_path,
                        n_ctx=2048,
                        verbose=False
                    )
                    self.is_fallback_active = True
                    self.active_model_name = self.fallback_model_name
                    print(f"Successfully initialized fallback model: {self.fallback_model_name}")
                except Exception as fallback_err:
                    print(f"Critical: Fallback model initialization also failed: {str(fallback_err)}")
                    raise fallback_err

    def format_instruction(self, user_prompt: str, system_prompt: str = "") -> str:
        """Applies chat templates matching the active model architecture (Gemma vs. Phi-3)."""
        active_lower = self.active_model_name.lower()
        if "phi" in active_lower:
            # Phi-3.1-mini template format
            prompt_str = ""
            if system_prompt:
                prompt_str += f"<|system|>\n{system_prompt}<|end|>\n"
            prompt_str += f"<|user|>\n{user_prompt}<|end|>\n<|assistant|>\n"
            return prompt_str
        else:
            # Gemma template format
            prompt_str = ""
            if system_prompt:
                prompt_str += f"<start_of_turn>user\nSystem Context:\n{system_prompt}\n\n{user_prompt}<end_of_turn>\n"
            else:
                prompt_str += f"<start_of_turn>user\n{user_prompt}<end_of_turn>\n"
            prompt_str += "<start_of_turn>model\n"
            return prompt_str

    def generate_completion(self, user_prompt: str, system_prompt: str = "", max_tokens: int = 512, temperature: float = 0.1) -> str:
        """Executes LLM inference, counts tokens consumed, and logs latency telemetry."""
        if not self.llm:
            raise ValueError("Model is not loaded. Please call load_model() first.")

        prompt = self.format_instruction(user_prompt, system_prompt)

        with telemetry_span("llm_inference") as span:
            response = self.llm(
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=["<|end|>", "<end_of_turn>", "<|user|>"]
            )
            
            # Extract generation and usage metrics
            text = response["choices"][0]["text"].strip()
            total_tokens = response.get("usage", {}).get("total_tokens", 0)
            
            span.token_count = total_tokens
            span.message = f"LLM inference completed via {self.active_model_name}. Tokens consumed: {total_tokens}."
            
            return text

# Global singleton manager
model_manager = ModelManager()
