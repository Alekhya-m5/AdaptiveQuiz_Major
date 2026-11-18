"""
Model Service for Qwen3-0.6B + LoRA Adapter.
Singleton architecture: loads base model and attaches pilot LoRA adapter ONCE,
reusing the in-memory instance across all subsequent quiz generation requests.
Engineered for Mac 8 GB RAM with graceful error isolation.
"""

import json
import logging
import os
import time
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_BASE_MODEL = os.getenv("BASE_MODEL_NAME", "Qwen/Qwen3-0.6B")
DEFAULT_ADAPTER_PATH = os.getenv("LORA_ADAPTER_PATH", "/Users/lokeshk/Downloads/fine_tuned_qwen_lora")


class QwenLoRAModelService:
    """
    Singleton service managing the lifecycle of the local Qwen base model
    and fine-tuned LoRA adapter.
    """
    _instance: Optional["QwenLoRAModelService"] = None

    def __init__(
        self,
        base_model_name: str = DEFAULT_BASE_MODEL,
        adapter_path: str = DEFAULT_ADAPTER_PATH,
    ):
        self.base_model_name = base_model_name
        self.adapter_path = adapter_path
        self.model = None
        self.tokenizer = None
        self.device = "cpu"
        self._is_loaded = False
        self._load_error: Optional[str] = None
        self._load_time: float = 0.0

    @classmethod
    def get_instance(cls) -> "QwenLoRAModelService":
        """Access or initialize the singleton model service."""
        if cls._instance is None:
            cls._instance = QwenLoRAModelService()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset singleton (useful in test teardown or memory cleanup)."""
        if cls._instance is not None:
            cls._instance.unload()
            cls._instance = None

    def is_loaded(self) -> bool:
        """Check if model and adapter are currently loaded in memory."""
        return self._is_loaded and self.model is not None

    def get_status(self) -> Dict[str, Any]:
        """Diagnostic status report of the model service."""
        return {
            "base_model": self.base_model_name,
            "adapter_path": self.adapter_path,
            "adapter_exists": os.path.isdir(self.adapter_path),
            "is_loaded": self._is_loaded,
            "device": self.device,
            "load_time_seconds": round(self._load_time, 2),
            "load_error": self._load_error,
        }

    def load_model(self) -> Tuple[bool, Optional[str]]:
        """
        Load the Qwen base model and attach the LoRA adapter once.
        Memory-conscious configuration suitable for 8 GB RAM Mac.
        """
        if self._is_loaded and self.model is not None:
            return True, None

        start_time = time.time()
        logger.info(f"Initializing Qwen + LoRA service: Base='{self.base_model_name}', Adapter='{self.adapter_path}'")

        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from peft import PeftModel
        except ImportError as e:
            self._load_error = f"Missing required ML dependencies (torch, transformers, peft): {e}"
            logger.warning(self._load_error)
            return False, self._load_error

        # Determine optimal device for Mac (mps if supported, else cpu)
        if torch.backends.mps.is_available():
            self.device = "mps"
        else:
            self.device = "cpu"

        logger.info(f"Targeting device '{self.device}' for Qwen3-0.6B inference.")

        try:
            # 1. Load Tokenizer
            # If local adapter directory has tokenizer files, load from adapter path, else base model
            tokenizer_path = self.adapter_path if os.path.exists(os.path.join(self.adapter_path, "tokenizer.json")) else self.base_model_name
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(
                    tokenizer_path,
                    trust_remote_code=True,
                    padding_side="left",
                )
            except Exception as tok_err:
                logger.warning(f"AutoTokenizer failed for '{tokenizer_path}': {tok_err}. Loading via fast tokenizer fallback...")
                tok_json_path = os.path.join(self.adapter_path, "tokenizer.json")
                if os.path.exists(tok_json_path):
                    from transformers import PreTrainedTokenizerFast
                    self.tokenizer = PreTrainedTokenizerFast(
                        tokenizer_file=tok_json_path,
                        eos_token="<|im_end|>",
                        pad_token="<|endoftext|>",
                        padding_side="left",
                    )
                else:
                    self.tokenizer = AutoTokenizer.from_pretrained(
                        self.base_model_name,
                        trust_remote_code=True,
                        padding_side="left",
                    )
            if self.tokenizer.pad_token is None:
                self.tokenizer.pad_token = self.tokenizer.eos_token

            # 2. Load Base Model
            logger.info(f"Loading base model '{self.base_model_name}'...")
            base_model = AutoModelForCausalLM.from_pretrained(
                self.base_model_name,
                device_map=self.device,
                torch_dtype=torch.float32,  # CPU / Mac stable precision
                trust_remote_code=True,
                low_cpu_mem_usage=True,
            )

            # 3. Attach LoRA Adapter
            if os.path.exists(self.adapter_path) and os.path.exists(os.path.join(self.adapter_path, "adapter_config.json")):
                logger.info(f"Attaching LoRA adapter from '{self.adapter_path}'...")
                self.model = PeftModel.from_pretrained(
                    base_model,
                    self.adapter_path,
                    is_trainable=False,
                )
            else:
                logger.warning(f"LoRA adapter path '{self.adapter_path}' not found; using base model directly.")
                self.model = base_model

            self.model.eval()
            self._is_loaded = True
            self._load_error = None
            self._load_time = time.time() - start_time
            logger.info(f"Qwen + LoRA successfully loaded into memory in {self._load_time:.2f}s.")
            return True, None

        except Exception as e:
            self._is_loaded = False
            self._load_error = f"Error during model loading: {e}"
            logger.error(self._load_error, exc_info=True)
            return False, self._load_error

    def generate(
        self,
        prompt: str,
        max_new_tokens: int = 350,
        temperature: float = 0.7,
        top_p: float = 0.9,
    ) -> str:
        """
        Execute inference on the loaded model.
        Returns the raw generated text string.
        """
        if not self.is_loaded():
            success, err = self.load_model()
            if not success:
                raise RuntimeError(f"Cannot generate: {err}")

        import torch

        start_time = time.time()
        try:
            inputs = self.tokenizer(prompt, return_tensors="pt")
            # Filter out token_type_ids which causal LMs like Qwen do not accept
            inputs = {k: v.to(self.device) for k, v in inputs.items() if k != "token_type_ids"}

            with torch.no_grad():
                outputs = self.model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    temperature=max(0.01, temperature),
                    top_p=top_p,
                    do_sample=temperature > 0.05,
                    pad_token_id=self.tokenizer.pad_token_id,
                    eos_token_id=self.tokenizer.eos_token_id,
                )

            # Decode only the generated completion tokens
            input_length = inputs["input_ids"].shape[1]
            generated_tokens = outputs[0][input_length:]
            raw_text = self.tokenizer.decode(generated_tokens, skip_special_tokens=False)

            elapsed = time.time() - start_time
            logger.info(f"Generation completed in {elapsed:.2f}s ({len(generated_tokens)} tokens generated).")
            return raw_text

        except Exception as e:
            logger.error(f"Inference error during generation: {e}", exc_info=True)
            raise

    def unload(self):
        """Release model weights from memory."""
        self.model = None
        self.tokenizer = None
        self._is_loaded = False
        self._load_error = None
        try:
            import gc
            gc.collect()
        except Exception:
            pass
