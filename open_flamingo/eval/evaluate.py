import argparse
import importlib
import json
import os
import random
import uuid
from collections import defaultdict
from typing import Callable

import more_itertools
import numpy as np
import torch
from coco_metric import compute_cider, postprocess_captioning_generation
from eval_datasets import CaptionDataset, VQADataset, ImageNetDataset
from tqdm import tqdm

from open_flamingo.eval.ok_vqa_utils import postprocess_ok_vqa_generation
from vqa_metric import compute_vqa_accuracy, postprocess_vqa_generation
from open_flamingo.src.flamingo import Flamingo
from open_flamingo.eval.imagenet_utils import (
    openai_imagenet_classnames,
    IMAGENET_1K_CLASS_ID_TO_LABEL,
)
from open_flamingo.eval import eval_model

parser = argparse.ArgumentParser()
parser.add_argument(
    "--results_file", type=str, default=None, help="JSON file to save results"
)

# Trial arguments
parser.add_argument("--shots", nargs="+", default=[0, 4, 8, 16, 32], type=int)
parser.add_argument(
    "--num_trials",
    type=int,
    default=1,
    help="Number of trials to run for each shot using different demonstrations",
)
parser.add_argument(
    "--trial_seeds",
    nargs="+",
    default=[42],
    help="Seeds to use for each trial for picking demonstrations and eval sets",
)
parser.add_argument(
    "--num_samples", type=int, default=5000, help="Number of samples to evaluate on"
)
parser.add_argument(
    "--query_set_size", type=int, default=2048, help="Size of demonstration query set"
)

parser.add_argument("--batch_size", type=int, default=8)

# Per-dataset evaluation flags
parser.add_argument(
    "--eval_coco",
    action="store_true",
    default=False,
    help="Whether to evaluate on COCO.",
)
parser.add_argument(
    "--eval_vqav2",
    action="store_true",
    default=False,
    help="Whether to evaluate on VQAV2.",
)
parser.add_argument(
    "--eval_ok_vqa",
    action="store_true",
    default=False,
    help="Whether to evaluate on OK-VQA.",
)
parser.add_argument(
    "--eval_imagenet",
    action="store_true",
    default=False,
    help="Whether to evaluate on ImageNet.",
)

parser.add_argument(
    "--eval_flickr30",
    action="store_true",
    default=False,
    help="Whether to evaluate on Flickr30.",
)

# Dataset arguments

## Flickr30 Dataset
parser.add_argument(
    "--flickr_image_dir_path",
    type=str,
    help="Path to the flickr30/flickr30k_images directory.",
    default=None,
)
parser.add_argument(
    "--flickr_karpathy_json_path",
    type=str,
    help="Path to the dataset_flickr30k.json file.",
    default=None,
)
parser.add_argument(
    "--flickr_annotations_json_path",
    type=str,
    help="Path to the dataset_flickr30k_coco_style.json file.",
)
## COCO Dataset
parser.add_argument(
    "--coco_train_image_dir_path",
    type=str,
    default=None,
)
parser.add_argument(
    "--coco_val_image_dir_path",
    type=str,
    default=None,
)
parser.add_argument(
    "--coco_karpathy_json_path",
    type=str,
    default=None,
)
parser.add_argument(
    "--coco_annotations_json_path",
    type=str,
    default=None,
)

## VQAV2 Dataset
parser.add_argument(
    "--vqav2_train_image_dir_path",
    type=str,
    default=None,
)
parser.add_argument(
    "--vqav2_train_questions_json_path",
    type=str,
    default=None,
)
parser.add_argument(
    "--vqav2_train_annotations_json_path",
    type=str,
    default=None,
)
parser.add_argument(
    "--vqav2_test_image_dir_path",
    type=str,
    default=None,
)
parser.add_argument(
    "--vqav2_test_questions_json_path",
    type=str,
    default=None,
)
parser.add_argument(
    "--vqav2_test_annotations_json_path",
    type=str,
    default=None,
)

## OK-VQA Dataset
parser.add_argument(
    "--ok_vqa_train_image_dir_path",
    type=str,
    help="Path to the vqav2/train2014 directory.",
    default=None,
)
parser.add_argument(
    "--ok_vqa_train_questions_json_path",
    type=str,
    help="Path to the v2_OpenEnded_mscoco_train2014_questions.json file.",
    default=None,
)
parser.add_argument(
    "--ok_vqa_train_annotations_json_path",
    type=str,
    help="Path to the v2_mscoco_train2014_annotations.json file.",
    default=None,
)
parser.add_argument(
    "--ok_vqa_test_image_dir_path",
    type=str,
    help="Path to the vqav2/val2014 directory.",
    default=None,
)
parser.add_argument(
    "--ok_vqa_test_questions_json_path",
    type=str,
    help="Path to the v2_OpenEnded_mscoco_val2014_questions.json file.",
    default=None,
)
parser.add_argument(
    "--ok_vqa_test_annotations_json_path",
    type=str,
    help="Path to the v2_mscoco_val2014_annotations.json file.",
    default=None,
)

## Imagenet dataset
parser.add_argument("--imagenet_root", type=str, default="/tmp")

parser.add_argument(
    "--model",
    type=str,
    help="Model name. Currently only `OpenFlamingo` is supported.",
    default="open_flamingo",
)


def main():
    args, leftovers = parser.parse_known_args()
    module = importlib.import_module(f"open_flamingo.eval.models.{args.model}")
    eval_model = module.EvalModel(leftovers)

    if len(args.trial_seeds) < args.num_trials:
        print(
            f"Number of trial seeds ({len(args.trial_seeds)}) is less than number of trials ({args.num_trials})."
        )
        print("Appending random seeds to trial seeds.")
        args.trial_seeds.extend(
            [
                random.randint(0, 1000000)
                for _ in range(args.num_trials - len(args.trial_seeds))
            ]
        )
        print(f"Trial seeds: {args.trial_seeds}")

    results = defaultdict(list)

    if args.eval_flickr30:
        print("Evaluating on Flickr30k...")
        for shot in args.shots:
            scores = []
            for seed, trial in zip(args.trial_seeds, range(args.num_trials)):
                cider_score = evaluate_captioning(
                    args,
                    eval_model=eval_model,
                    num_shots=shot,
                    seed=seed,
                    dataset_name="flickr",
                )
                print(f"Shots {shot} Trial {trial} CIDEr score: {cider_score}")
                scores.append(cider_score)
            print(f"Shots {shot} Mean CIDEr score: {np.mean(scores)}")
            results["flickr30"].append(
                {"shots": shot, "trials": scores, "mean": np.mean(scores)}
            )

    if args.eval_coco:
        print("Evaluating on COCO...")
        for shot in args.shots:
            scores = []
            for seed, trial in zip(args.trial_seeds, range(args.num_trials)):
                cider_score = evaluate_captioning(
                    args,
                    eval_model=eval_model,
                    num_shots=shot,
                    seed=seed,
                    dataset_name="coco",
                )
                print(f"Shots {shot} Trial {trial} CIDEr score: {cider_score}")
                scores.append(cider_score)
            print(f"Shots {shot} Mean CIDEr score: {np.mean(scores)}")
            results["coco"].append(
                {"shots": shot, "trials": scores, "mean": np.mean(scores)}
            )

    if args.eval_ok_vqa:
        print("Evaluating on OK-VQA...")
        for shot in args.shots:
            scores = []
            for seed, trial in zip(args.trial_seeds, range(args.num_trials)):
                ok_vqa_score = evaluate_vqa(
                    args=args,
                    eval_model=eval_model,
                    num_shots=shot,
                    seed=seed,
                    vqa_dataset="ok_vqa",
                )
                print(f"Shots {shot} Trial {trial} OK-VQA score: {ok_vqa_score}")
                scores.append(ok_vqa_score)
            print(f"Shots {shot} Mean OK-VQA score: {np.mean(scores)}")
            results["ok_vqa"].append(
                {"shots": shot, "trials": scores, "mean": np.mean(scores)}
            )

    if args.eval_vqav2:
        print("Evaluating on VQAv2...")
        for shot in args.shots:
            scores = []
            for seed, trial in zip(args.trial_seeds, range(args.num_trials)):
                vqa_score = evaluate_vqa(
                    args=args,
                    eval_model=eval_model,
                    num_shots=shot,
                    seed=seed,
                    vqa_dataset="vqav2",
                )
                print(f"Shots {shot} Trial {trial} VQA score: {vqa_score}")
                scores.append(vqa_score)
            print(f"Shots {shot} Mean VQA score: {np.mean(scores)}")
            results["vqav2"].append(
                {"shots": shot, "trials": scores, "mean": np.mean(scores)}
            )

    if args.eval_imagenet:
        print("Evaluating on ImageNet...")
        for shot in args.shots:
            scores = []
            for seed, trial in zip(args.trial_seeds, range(args.num_trials)):
                imagenet_score = evaluate_imagenet(
                    eval_model=eval_model,
                    batch_size=args.batch_size,
                    num_samples=args.num_samples,
                    num_shots=shot,
                    seed=seed,
                    imagenet_root=args.imagenet_root,
                )
                print(
                    f"Shots {shot} Trial {trial} " f"ImageNet score: {imagenet_score}"
                )
                scores.append(imagenet_score)
            print(f"Shots {shot} Mean ImageNet score: {np.mean(scores)}")
            results["imagenet"].append(
                {"shots": shot, "trials": scores, "mean": np.mean(scores)}
            )

    if args.results_file is not None:
        with open(args.results_file, "w") as f:
            json.dump(results, f)


def get_random_indices(num_samples, query_set_size, full_dataset, seed):
    if num_samples + query_set_size > len(full_dataset):
        raise ValueError(
            f"num_samples + num_shots must be less than {len(full_dataset)}"
        )

    # get a random subset of the dataset
    np.random.seed(seed)
    random_indices = np.random.choice(
        len(full_dataset), num_samples + query_set_size, replace=False
    )
    return random_indices


def get_query_set(train_dataset, query_set_size, seed):
    np.random.seed(seed)
    query_set = np.random.choice(len(train_dataset), query_set_size, replace=False)
    return [train_dataset[i] for i in query_set]


def prepare_eval_samples(test_dataset, num_samples, seed):
    np.random.seed(seed)
    random_indices = np.random.choice(len(test_dataset), num_samples, replace=False)
    return torch.utils.data.Subset(test_dataset, random_indices)


def sample_batch_demos_from_query_set(query_set, num_samples, batch_size):
    return [random.sample(query_set, num_samples) for _ in range(batch_size)]


def evaluate_captioning(
    args,
    eval_model,
    seed=42,
    max_generation_length=20,
    num_beams=3,
    length_penalty=-2.0,
    query_set_size=2048,
    num_shots=8,
    dataset_name="coco",
):
    """Evaluate a model on COCO dataset.

    Args:
        args (argparse.Namespace): arguments
        eval_model (eval_model.EvalModel): model to evaluate
        seed (int, optional): seed for random number generator. Defaults to 42.
        max_generation_length (int, optional): maximum length of the generated caption. Defaults to 10.
        num_beams (int, optional): number of beams to use for beam search. Defaults to 3.
        length_penalty (float, optional): length penalty for beam search. Defaults to -2.0.
        query_set_size (int, optional): number of samples to use for query set. Defaults to 2048.
        num_shots (int, optional): number of in-context samples to use. Defaults to 8.
        num_workers (int, optional): number of workers to use for dataloader. Defaults to 4.
        dataset_name (str, optional): dataset to evaluate on. Defaults to "coco". Can be "coco" or "flickr".
    Returns:
        float: CIDEr score

    """

    train_dataset = CaptionDataset(
        image_train_dir_path=args.coco_train_image_dir_path
        if dataset_name == "coco"
        else args.flickr_image_dir_path,
        image_val_dir_path=args.coco_val_image_dir_path
        if dataset_name == "coco"
        else None,
        annotations_path=args.coco_karpathy_json_path
        if dataset_name == "coco"
        else args.flickr_karpathy_json_path,
        is_train=True,
        dataset_name=dataset_name,
    )

    test_dataset = CaptionDataset(
        image_train_dir_path=args.coco_train_image_dir_path
        if dataset_name == "coco"
        else args.flickr_image_dir_path,
        image_val_dir_path=args.coco_val_image_dir_path
        if dataset_name == "coco"
        else None,
        annotations_path=args.coco_karpathy_json_path
        if dataset_name == "coco"
        else args.flickr_karpathy_json_path,
        is_train=False,
        dataset_name=dataset_name,
    )

    effective_num_shots = num_shots if num_shots > 0 else 2

    test_dataset = prepare_eval_samples(
        test_dataset,
        args.num_samples if args.num_samples > 0 else len(test_dataset),
        seed,
    )

    in_context_samples = get_query_set(train_dataset, args.query_set_size, seed)

    predictions = defaultdict()

    desc = (
        "Running inference Flickr30k"
        if dataset_name == "flickr"
        else "Running inference MS-COCO"
    )

    for batch in more_itertools.chunked(tqdm(test_dataset, desc=desc), args.batch_size):
        batch_demo_samples = sample_batch_demos_from_query_set(
            in_context_samples, effective_num_shots, len(batch)
        )

        batch_images = []
        batch_text = []
        for i in range(len(batch)):
            if num_shots > 0:
                context_images = [x["image"] for x in batch_demo_samples[i]]
            else:
                context_images = []
            batch_images.append(context_images + [batch[i]["image"]])

            context_text = "".join(
                [
                    eval_model.caption_prompt(caption=x["caption"].strip())
                    for x in batch_demo_samples[i]
                ]
            )

            # Keep the text but remove the image tags for the zero-shot case
            if num_shots == 0:
                context_text = context_text.replace("<image>", "")

            batch_text.append(context_text + eval_model.caption_prompt())

        outputs = eval_model.get_outputs(
            batch_images=batch_images,
            batch_text=batch_text,
            max_generation_length=max_generation_length,
            num_beams=num_beams,
            length_penalty=length_penalty,
        )

        new_predictions = [
            postprocess_captioning_generation(out).replace('"', "") for out in outputs
        ]
        
        print(new_predictions)

        for i, sample in enumerate(batch):
            predictions[sample["image_id"]] = {
                "caption": new_predictions[i],
            }

    # save the predictions to a temporary file
    results_path = f"{dataset_name}results_{uuid.uuid4()}.json"

    with open(results_path, "w") as f:
        f.write(
            json.dumps(
                [
                    {"image_id": k, "caption": predictions[k]["caption"]}
                    for k in predictions
                ],
                indent=4,
            )
        )

    metrics = compute_cider(
        result_path=results_path,
        annotations_path=args.coco_annotations_json_path
        if dataset_name == "coco"
        else args.flickr_annotations_json_path,
    )

    # delete the temporary file
    os.remove(results_path)

    return metrics["CIDEr"] * 100.0


def evaluate_vqa(
    args,
    eval_model,
    seed=42,
    max_generation_length=5,
    num_beams=3,
    length_penalty=-2.0,
    num_shots=8,
    vqa_dataset="vqav2",
):
    """
    Evaluate a model on VQA datasets. Currently supports VQA v2.0.

    Args:
        args (argparse.Namespace): arguments
        eval_model (eval_model.EvalModel): model to evaluate
        seed (int, optional): random seed. Defaults to 42.
        max_generation_length (int, optional): max generation length. Defaults to 5.
        num_beams (int, optional): number of beams to use for beam search. Defaults to 3.
        length_penalty (float, optional): length penalty for beam search. Defaults to -2.0.
        num_shots (int, optional): number of shots to use. Defaults to 8.
        num_workers (int, optional): number of workers to use. Defaults to 4.
        vqa_dataset (string): type of vqa dataset: currently supports vqa, ok_vqa. Defaults to vqa.
    Returns:
        float: accuracy score
    """

    train_dataset = VQADataset(
        image_dir_path=args.ok_vqa_train_image_dir_path
        if vqa_dataset == "ok_vqa"
        else args.vqav2_train_image_dir_path,
        question_path=args.ok_vqa_train_questions_json_path
        if vqa_dataset == "ok_vqa"
        else args.vqav2_train_questions_json_path,
        annotations_path=args.ok_vqa_train_annotations_json_path
        if vqa_dataset == "ok_vqa"
        else args.vqav2_train_annotations_json_path,
        is_train=True,
    )

    test_dataset = VQADataset(
        image_dir_path=args.ok_vqa_test_image_dir_path
        if vqa_dataset == "ok_vqa"
        else args.vqav2_test_image_dir_path,
        question_path=args.ok_vqa_test_questions_json_path
        if vqa_dataset == "ok_vqa"
        else args.vqav2_test_questions_json_path,
        annotations_path=args.ok_vqa_test_annotations_json_path
        if vqa_dataset == "ok_vqa"
        else args.vqav2_test_annotations_json_path,
        is_train=False,
    )

    effective_num_shots = num_shots if num_shots > 0 else 2

    test_dataset = prepare_eval_samples(
        test_dataset,
        args.num_samples if args.num_samples > 0 else len(test_dataset),
        seed,
    )

    in_context_samples = get_query_set(train_dataset, args.query_set_size, seed)
    predictions = []

    for batch in more_itertools.chunked(
        tqdm(test_dataset, desc="Running inference"), args.batch_size
    ):
        batch_demo_samples = sample_batch_demos_from_query_set(
            in_context_samples, effective_num_shots, len(batch)
        )

        batch_images = []
        batch_text = []
        for i in range(len(batch)):
            if num_shots > 0:
                context_images = [x["image"] for x in batch_demo_samples[i]]
            else:
                context_images = []
            batch_images.append(context_images + [batch[i]["image"]])

            context_text = "".join(
                [
                    eval_model.vqa_prompt(
                        question=x["question"], answer=x["answers"][0]
                    )
                    for x in batch_demo_samples[i]
                ]
            )

            # Keep the text but remove the image tags for the zero-shot case
            if num_shots == 0:
                context_text = context_text.replace("<image>", "")

            batch_text.append(
                context_text + eval_model.vqa_prompt(question=batch[i]["question"])
            )

        outputs = eval_model.get_outputs(
            batch_images=batch_images,
            batch_text=batch_text,
            max_generation_length=max_generation_length,
            num_beams=num_beams,
            length_penalty=length_penalty,
        )

        process_function = (
            postprocess_vqa_generation
            if vqa_dataset == "vqav2"
            else postprocess_ok_vqa_generation
        )

        new_predictions = map(process_function, outputs)

        predictions.extend(
            [
                {"answer": p, "question_id": sample["question_id"]}
                for p, sample in zip(new_predictions, batch)
            ]
        )
    # save the predictions to a temporary file
    random_uuid = str(uuid.uuid4())
    with open(f"{vqa_dataset}results_{random_uuid}.json", "w") as f:
        f.write(json.dumps(predictions, indent=4))

    acc = compute_vqa_accuracy(
        f"{vqa_dataset}results_{random_uuid}.json",
        args.ok_vqa_test_questions_json_path
        if vqa_dataset == "ok_vqa"
        else args.vqav2_test_questions_json_path,
        args.ok_vqa_test_annotations_json_path
        if vqa_dataset == "ok_vqa"
        else args.vqav2_test_annotations_json_path,
    )

    # delete the temporary file
    os.remove(f"{vqa_dataset}results_{random_uuid}.json")

    return acc


def find_sub_list(sl, l):
    results = []
    sll = len(sl)
    for ind in (i for i, e in enumerate(l) if e == sl[0]):
        if l[ind : ind + sll] == sl:
            results.append(ind + sll - 1)
    return results


def evaluate_imagenet(
    eval_model,
    batch_size: int,
    imagenet_root: str,
    seed: int = 42,
    num_samples: int = 5000,
    num_shots: int = 8,
):
    """
    Evaluate a model on ImageNet dataset.

    Args:
        eval_model (eval_model.EvalModel): model to evaluate
        batch_size (int): batch size
        imagenet_root (str): path to imagenet root for the specified split.
        seed (int, optional): random seed. Defaults to 42.
        num_samples (int, optional): number of samples to evaluate on. Defaults to 5000 samples.
        num_shots (int, optional): number of shots to use. Defaults to 8.

    Returns:
        float: accuracy score
    """
    if not hasattr(eval_model, "model") or not hasattr(eval_model, "tokenizer"):
        raise NotImplementedError(
            "evaluate_imagenet is currently only supported for OpenFlamingo " "models"
        )
    model, tokenizer = eval_model.model, eval_model.tokenizer
    assert isinstance(model, Flamingo)

    train_dataset = ImageNetDataset(os.path.join(imagenet_root, "train"))
    val_dataset = ImageNetDataset(os.path.join(imagenet_root, "val"))

    effective_num_shots = num_shots if num_shots > 0 else 2
    tokenizer.padding_side = (
        "left"  # For generation padding tokens should be on the left
    )

    acc1 = 0
    acc5 = 0
    prompt_text = "<image>A photo of a"

    for i, batch in enumerate(val_dataset):
        # Choose a different set of random context samples for each batch
        # from the training set
        context_indices = np.random.choice(
            len(train_dataset), effective_num_shots, replace=False
        )

        in_context_samples = [train_dataset[i] for i in context_indices]

        vision_x = [
            eval_model.image_processor(data["image"]).unsqueeze(0)
            for data in in_context_samples
        ] + [eval_model.image_processor(batch["image"]).unsqueeze(0)]
        vision_x = torch.cat(vision_x, dim=0)
        vision_x = vision_x.unsqueeze(1).unsqueeze(0)
        model._encode_vision_x(vision_x.cuda())

        context_class_names = [
            in_context_samples[i]["class_name"] for i in range(effective_num_shots)
        ]
        context_text = "".join(
            f"{prompt_text} {classname}<|endofchunk|>"
            for classname in context_class_names
        )

        # TODO(jpgard): cache the context text here, and compute the outputs
        #  one token at a time by using Flamingo.forward() with
        #  past_key_values and use_cache parameters.

        overall_probs = []
        for imagenet_class_name in tqdm(openai_imagenet_classnames):
            target_text = f"{prompt_text} {imagenet_class_name}"
            prompt_tokens = (
                tokenizer(prompt_text, add_special_tokens=False, return_tensors="np")[
                    "input_ids"
                ]
                .ravel()
                .tolist()
            )

            lang_x = tokenizer([context_text + target_text], return_tensors="pt")

            outputs = model(
                vision_x=None,
                lang_x=lang_x["input_ids"].cuda(),
                attention_mask=lang_x["attention_mask"].cuda(),
                clear_conditioned_layers=False,
                use_cached_vision_x=True,
            )
            probs = torch.softmax(outputs.logits, dim=-1).detach()
            # collect the probability of the generated token -- probability
            # at index 0 corresponds to the token at index 1
            probs = probs[:, :-1, :]
            input_ids = lang_x["input_ids"][:, 1:].cuda()
            gen_probs = torch.gather(probs, 2, input_ids[:, :, None]).squeeze(-1)

            probs = []
            for input_sentence, input_probs in zip(input_ids, gen_probs):
                idxes = find_sub_list(
                    prompt_tokens, input_sentence.detach().cpu().numpy().tolist()
                )
                input_probs = input_probs[idxes[-1] + 1 :]
                probs.append(torch.prod(input_probs).item())
            overall_probs.append(probs)

        top5 = [
            IMAGENET_1K_CLASS_ID_TO_LABEL[pred]
            for pred in np.argsort(np.array(overall_probs)[:, 0])[::-1][:5]
        ]
        if batch["class_name"] == top5[0]:
            acc1 += 1
        if batch["class_name"] in top5:
            acc5 += 1
        print(
            "eval {}/{}: acc@1 ({}), acc@5 ({})".format(
                i, num_samples, acc1 / (i + 1), acc5 / (i + 1)
            )
        )
        if i >= num_samples - 1:
            break

    return float(acc1) / num_samples


if __name__ == "__main__":
    main()
