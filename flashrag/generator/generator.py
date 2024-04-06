import torch
import torch.nn.functional as F
import torch.nn as nn
import collections
from typing import List, Dict
import re
import openai
from tqdm import tqdm
import torch
from copy import deepcopy
from torch import Tensor
from transformers import AutoTokenizer, AutoModel, AutoModelForCausalLM, T5ForConditionalGeneration, BartForConditionalGeneration
from fastchat.model import load_model


class BaseGenerator:
    r"""`BaseGenerator` is a base object of Generator model.
    
    """

    def __init__(self, config):
        self.model_name = config['generator_model']
        self.model_path = config['generator_model_path']

        self.max_input_len = config['generator_max_input_len']
        self.batch_size = config['generator_batch_size']
        self.device = config['device']
        self.gpu_num = torch.cuda.device_count()

        self.generation_params = config['generation_params']
    
    def generate(self, input_list: list) -> List[str]:
        r"""Get responses from the generater.

        Args:
            input_list: it contains input texts, each item represents a sample.
        
        Returns:
            list: contains generator's response of each input sample.
        """
        pass


class EncoderDecoderGenerator(BaseGenerator):
    r"""Class for encoder-decoder model"""
    def __init__(self, config):
        super().__init__(config)
        if "t5" in self.model_name: 
            self.model = T5ForConditionalGeneration.from_pretrained(self.model_path)
        else:
            self.model = BartForConditionalGeneration.from_pretrained(self.model_path)
        self.model.cuda()
        self.model.eval()
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_path)


    @torch.no_grad()
    def generate(self, input_list: List, batch_size=None, **params):
        if isinstance(input_list, str):
            input_list = [input_list]
        if batch_size is None:
            batch_size = self.batch_size
        
        generation_params = deepcopy(self.generation_params)
        generation_params.update(params)

        responses = []
        for idx in tqdm(range(0, len(input_list), batch_size), desc='Generation process: '):
            batched_prompts = input_list[idx:idx+batch_size]
            inputs = self.tokenizer(batched_prompts, 
                                    return_tensors="pt", 
                                    padding=True,
                                    truncation=True,
                                    max_length=self.max_input_len
                                ).to(self.device)
            
            # TODO: multi-gpu inference
            outputs = self.model.generate(
                **inputs,
                **generation_params
            )

            outputs = self.tokenizer.batch_decode(outputs, 
                                                  skip_special_tokens=True, 
                                                  clean_up_tokenization_spaces=False)

            responses += outputs

        return responses



class CausalLMGenerator(BaseGenerator):
    r"""Class for decoder-only generator. 
    
    """
    def __init__(self, config, model=None):
        super().__init__(config)
        lora_path = None if 'generator_lora_path' not in config else config['generator_lora_path']
        self.model, self.tokenizer = self._load_model(model=model)
        if lora_path is not None:
            import peft
            self.model.load_adapter(lora_path)
    
    def _load_model(self, model=None):
        r"""Load model and tokenizer for generator.
        
        """
        # TODO: try vllm
        # model = AutoModelForCausalLM.from_pretrained(self.model_path)
        # model = model.to(self.device)
        # tokenizer = AutoTokenizer.from_pretrained(self.model_path)
        if model is None:
            model, tokenizer = load_model(self.model_path,
                                            device = 'cuda', 
                                            num_gpus = self.gpu_num,
                                            load_8bit = False,
                                            cpu_offloading = False,
                                            debug = False,)
        else:
            model.cuda()
            tokenizer = AutoTokenizer.from_pretrained(self.model_path)
        model.eval()
        tokenizer.pad_token = tokenizer.eos_token
        tokenizer.padding_side = "left"

        return model, tokenizer
    
    @torch.no_grad()
    def generate(self, input_list, batch_size=None, **params):
        r"""Generate batches one by one. The generated content needs to exclude input.
    
        """
        if isinstance(input_list, str):
            input_list = [input_list]
        if batch_size is None:
            batch_size = self.batch_size

        generation_params = deepcopy(self.generation_params)
        generation_params.update(params)

        responses = []
        for idx in tqdm(range(0, len(input_list), batch_size), desc='Generation process: '):
            batched_prompts = input_list[idx:idx+batch_size]
            inputs = self.tokenizer(batched_prompts, 
                                    return_tensors="pt", 
                                    padding=True,
                                    truncation=True,
                                    max_length=self.max_input_len
                                ).to(self.model.device)
            outputs = self.model.generate(
                **inputs,
                **generation_params
            )
            for i, generated_sequence in enumerate(outputs):
                input_ids = inputs['input_ids'][i]
                text = self.tokenizer.decode(
                            generated_sequence, 
                            skip_special_tokens=True, 
                            clean_up_tokenization_spaces=False
                        )
                if input_ids is None:
                    prompt_length = 0
                else:
                    prompt_length = len(
                        self.tokenizer.decode(
                            input_ids,
                            skip_special_tokens=True,
                            clean_up_tokenization_spaces=False,
                        )
                    )
                new_text = text[prompt_length:]
                responses.append(new_text.strip())
        
        return responses
