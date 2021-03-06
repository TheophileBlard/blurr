# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/02e_modeling-summarization.ipynb (unless otherwise specified).

__all__ = ['calculate_rouge', 'HF_SummarizationModelCallback', 'summarization_splitter', 'HF_MaskedLMLoss']

# Cell
import ast, torch
from transformers import *
from fastai.text.all import *

from ..data.all import *
from .core import *

from rouge_score import rouge_scorer, scoring

# Cell
def calculate_rouge(predicted_txts, reference_txts, rouge_keys=["rouge1", "rouge2", "rougeL"], use_stemmer=True):
    scorer = rouge_scorer.RougeScorer(rouge_keys, use_stemmer=use_stemmer)
    aggregator = scoring.BootstrapAggregator()

    for ref_text, pred_txt in zip(reference_txts, predicted_txts):
        scores = scorer.score(ref_text, pred_txt)
        aggregator.add_scores(scores)

    result = aggregator.aggregate()
    return result

# Cell
class HF_SummarizationModelCallback(HF_BaseModelCallback):
    def __init__(self, rouge_metrics=["rouge1", "rouge2", "rougeL"], text_gen_kwargs={}, **kwargs):
        self.run_before = Recorder

        store_attr(self=self, names='rouge_metrics, text_gen_kwargs, kwargs')
        self.custom_metrics_dict = { k:None for k in rouge_metrics }

        self.do_setup = True

    def setup(self):
        # one time setup code here.
        if (not self.do_setup): return

        # grab the hf_tokenizer from the target's HF_TokenizerTransform (used for rouge metrics)
        hf_textblock_tfm = self.learn.dls.before_batch[0]
        self.hf_tokenizer = hf_textblock_tfm.hf_tokenizer
        self.tok_kwargs = hf_textblock_tfm.tok_kwargs

        # add custom text generation specific metrics
        custom_metric_keys = self.custom_metrics_dict.keys()
        custom_metrics = L([ ValueMetric(partial(self.metric_value, metric_key=k), k) for k in custom_metric_keys ])
        self.learn.metrics = self.learn.metrics + custom_metrics

        self.do_setup = False

    def before_fit(self): self.setup()


    # --- batch begin/after phases ---
    def after_batch(self):
        if (self.training or self.learn.y is None): return

        # grab predicted and reference ids for any metrics that need them
        input_ids, attention_mask = self.xb[0]['input_ids'], self.xb[0]['attention_mask']
        gen_ids = self.learn.model.hf_model.generate(input_ids=input_ids,
                                                     attention_mask=attention_mask,
                                                     use_cache=True,
                                                     **self.text_gen_kwargs)

        self.generated_ids += gen_ids.tolist()
        self.refernce_ids += self.yb[0].tolist()


    # --- validation begin/after phases ---
    def before_validate(self): self.generated_ids, self.refernce_ids = [], []

    def after_validate(self):
        # are there rouge metrics to calculate?
        if (self.rouge_metrics is not None and len(self.rouge_metrics) > 0):
            gen_texts = self.hf_tokenizer.batch_decode(self.generated_ids,
                                                       skip_special_tokens=True,
                                                       clean_up_tokenization_spaces=True)

            ref_texts = self.hf_tokenizer.batch_decode(self.refernce_ids,
                                                       skip_special_tokens=True,
                                                       clean_up_tokenization_spaces=True)

            rouge_results = calculate_rouge(gen_texts, ref_texts, rouge_keys=self.rouge_metrics)

            for rouge_key, scores in rouge_results.items():
                self.custom_metrics_dict[rouge_key] = scores.mid.fmeasure


    # --- for ValueMetric metrics ---
    def metric_value(self, metric_key): return self.custom_metrics_dict[metric_key]

# Cell
def summarization_splitter(m, arch):
    """Custom param splitter for summarization models"""
    model = m.hf_model if (hasattr(m, 'hf_model')) else m

    if arch in ['bart', 'pegasus']:
        embeds = nn.Sequential(
            model.model.shared,
            model.model.encoder.embed_positions,
            model.model.encoder.embed_tokens,
            model.model.decoder.embed_positions,
            model.model.decoder.embed_tokens
        )

        groups = L(embeds, model.model.encoder, model.model.decoder)
        return groups.map(params).filter(lambda el: len(el) > 0)

    if arch in['t5']:
        embeds = nn.Sequential(
            model.shared,
            model.encoder.embed_tokens,
            model.decoder.embed_tokens
        )

        groups = L(embeds, model.encoder, model.decoder)
        return groups.map(params).filter(lambda el: len(el) > 0)

    raise ValueError('Invalid architecture')

# Cell
class HF_MaskedLMLoss():
    def __call__(self, inp, targ, **kwargs): return
    def decodes(self, x): return x.argmax(dim=-1)
    def activation(self, x): return F.softmax(x, dim=-1)

# Cell
@patch
def blurr_summarize(self:Learner, inp, **kwargs):
    """Uses the built-in `generate` method to generate the text
    (see [here](https://huggingface.co/transformers/main_classes/model.html#transformers.PreTrainedModel.generate)
    for a list of arguments you can pass in)
    """
    # grab the text generation kwargs
    text_gen_kwargs = self.cbs.filter(lambda el: isinstance(el, HF_SummarizationModelCallback) )[0].text_gen_kwargs
    text_gen_kwargs = { **text_gen_kwargs, **kwargs}

    # grab the huggingface tokenizer from the learner's dls.tfms
    hf_textblock_tfm = self.dls.before_batch[0]
    hf_tokenizer = hf_textblock_tfm.hf_tokenizer
    tok_kwargs = hf_textblock_tfm.tok_kwargs

    if (isinstance(inp, str)):
        input_ids = hf_tokenizer.encode(inp, padding=True, truncation=True, return_tensors='pt', **tok_kwargs)
    else:
        input_ids = inp

    input_ids = input_ids.to(self.model.hf_model.device)

    gen_texts = self.model.hf_model.generate(input_ids, **text_gen_kwargs)
    outputs = [ hf_tokenizer.decode(txt, skip_special_tokens=True, clean_up_tokenization_spaces=False)
               for txt in gen_texts ]

    if hf_textblock_tfm.hf_arch == 'pegasus':
        outputs = [o.replace('<n>', ' ') for o in outputs]

    return outputs

# Cell
@typedispatch
def show_results(x:HF_SummarizationInput, y, samples, outs, learner, ctxs=None, max_n=6, **kwargs):
    hf_tokenizer = learner.dls.before_batch[0].hf_tokenizer

    gen_text_txts = learner.blurr_summarize(x)
    res = L([
        (hf_tokenizer.decode(s[0], skip_special_tokens=True),
         hf_tokenizer.decode(s[1], skip_special_tokens=True),
         gen_txt) for s, gen_txt in zip(samples, gen_text_txts) ])

    display_df(pd.DataFrame(res, columns=['text', 'target', 'prediction'])[:max_n])
    return ctxs