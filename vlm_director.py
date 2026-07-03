#!/usr/bin/env python
"""Closed-loop director: a local VLM (Qwen2-VL-2B) looks at the seam frame and writes
the next segment's prompt, with respect to the overall vision + anti-drift correction.
Lives on CPU; moved to GPU only for the brief analysis between LTX segments (time-share)."""
import torch
from transformers import Qwen2VLForConditionalGeneration, AutoProcessor

MODEL_ID = "Qwen/Qwen2-VL-2B-Instruct"


class Director:
    def __init__(self, model_id=MODEL_ID):
        self.model = Qwen2VLForConditionalGeneration.from_pretrained(model_id, torch_dtype=torch.bfloat16)
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model.eval()

    def to(self, device):
        self.model.to(device)
        return self

    @torch.no_grad()
    def next_prompt(self, frame_pil, directive, seg_idx, total_segs, anchors=""):
        instruction = (
            f"You are directing a short film. OVERALL VISION: {directive}\n"
            f"The image is the LAST FRAME of shot {seg_idx} of {total_segs}.\n"
            "Write the prompt for the NEXT shot so the video continues naturally from this exact frame "
            "and advances the vision. Keep the same subject, setting and visual style for consistency. "
            "If the image looks washed-out, blurry, or low-detail, add corrective words "
            "(vivid colors, sharp focus, high detail, strong contrast). "
            + (f"Always include these anchors: {anchors}. " if anchors else "")
            + "Reply with ONLY the next-shot prompt as one vivid line, UNDER 35 WORDS. No preamble, no quotes."
        )
        device = self.model.device
        messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": instruction}]}]
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.processor(text=[text], images=[frame_pil], return_tensors="pt").to(device)
        out = self.model.generate(**inputs, max_new_tokens=70, do_sample=False)
        reply = self.processor.batch_decode(out[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0]
        return " ".join(reply.split()).strip(' "\'')


if __name__ == "__main__":
    import sys
    from PIL import Image
    d = Director().to("cuda")
    img = Image.open(sys.argv[1]).convert("RGB")
    print("NEXT PROMPT:", d.next_prompt(img, "a red fox exploring a snowy forest at dawn", 1, 5, "red fox, snowy pine forest, cinematic"))
