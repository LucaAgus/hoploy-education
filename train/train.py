import argparse
import glob
import os
import shutil

from transformers.trainer_utils import get_last_checkpoint
from hopwise.quick_start import run_hopwise

# docker compose run train -c autism_test_hoploy -d autism
# CHECKPOINT_CONFIG=autism_test_hoploy DATASET=autism docker compose up train
parser = argparse.ArgumentParser()
parser.add_argument("-c", "--config", default=os.environ.get("CHECKPOINT_CONFIG", "autism_test_hoploy"))
parser.add_argument("-d", "--dataset", default=os.environ.get("DATASET", "autism"))
parser.add_argument("-m", "--model", default=os.environ.get("MODEL", "PEARLM")) # PEARLM, KGGLM
args = parser.parse_args()

config_name, dataset, model = args.config, args.dataset, args.model

checkpoint_dir  = f"/app/checkpoints/{config_name}"
config_file     = f"{checkpoint_dir}/config.yaml"           # modelli a stadio singolo
config_pretrain = f"{checkpoint_dir}/config_pretrain.yaml"  # KGGLM fase 1
config_finetune = f"{checkpoint_dir}/config_finetune.yaml"  # KGGLM fase 2
data_path       = "/app/datasets"

# hopwise serializza il Dataset e lo ricarica senza mai riallineargli la config:
# `train_stage` resta congelato al valore del run che ha scritto il pickle, e in
# fase di finetune fa campionare path di PRETRAIN, in silenzio.
# Disattivare la scrittura del pickle chiude il problema.
# `save_dataloaders` resta gestito dallo YAML: quella cache e' sicura una volta
# che save_dataset e' False, e ti risparmia ore di campionamento nei rilanci.
# ATTENZIONE: non si invalida se cambi MAX_PATHS_PER_USER o path_sample_args --
# in quel caso mettila a False nello YAML o svuota la cartella dataloaders.
NOCACHE = {"save_dataset": False}


def purge_dataset_cache():
    """Il pickle viene letto se esiste, a prescindere da save_dataset."""
    for f in glob.glob(f"{checkpoint_dir}/{dataset}-*Dataset.pth"):
        os.remove(f)
        print(f"[cache] rimosso {f}")


# kgglm pretrain + finetune
def run_kgglm():
    purge_dataset_cache()

    # Phase 1: pretrain
    run_hopwise(
        model=model,
        dataset=dataset,
        config_file_list=[config_pretrain],
        saved=True,
        config_dict={
            "train_stage": "pretrain",
            "data_path": data_path,
            "checkpoint_dir": checkpoint_dir,
            **NOCACHE,
        },
    )

    # Trova il checkpoint HF pretrained salvato da hopwise:
    # <checkpoint_dir>/huggingface-*-<model>-<dataset>-pretrained-<N>.pth/checkpoint-<step>/
    # Il pattern include modello e dataset per non pescare residui di altre run.
    # Ordina per mtime: lessicograficamente 'pretrained-9' verrebbe dopo 'pretrained-20'.
    candidates = sorted(
        glob.glob(f"{checkpoint_dir}/huggingface-*-{model}-{dataset}-pretrained-*.pth"),
        key=os.path.getmtime,
    )
    if not candidates:
        raise RuntimeError(f"No pretrained checkpoint found in {checkpoint_dir}")
    pre_model_path = get_last_checkpoint(candidates[-1])
    if pre_model_path is None:
        raise RuntimeError(f"No valid checkpoint-* subdir found in {candidates[-1]}")
    print(f"[pretrain] checkpoint: {pre_model_path}")

    # Phase 2: finetune
    run_hopwise(
        model=model,
        dataset=dataset,
        config_file_list=[config_finetune],
        saved=True,
        config_dict={
            "train_stage": "finetune",
            "pre_model_path": pre_model_path,
            "data_path": data_path,
            "checkpoint_dir": checkpoint_dir,
            **NOCACHE,
        },
    )

    # Elimina tutte le directory pretrained intermedie dopo il finetune
    for d in glob.glob(f"{checkpoint_dir}/huggingface-*-{model}-{dataset}-pretrained-*.pth"):
        shutil.rmtree(d, ignore_errors=True)


def run_default():
    #purge_dataset_cache()

    run_hopwise(
        model=model,
        dataset=dataset,
        config_file_list=[config_file],
        saved=True,
        config_dict={
            "data_path": data_path,
            "checkpoint_dir": checkpoint_dir,
            #**NOCACHE,
        },
    )


map = {
    "KGGLM": run_kgglm,
}

if model in map:
    map[model]()
else:
    run_default()