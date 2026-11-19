<!-- Synthetic example only. 不是正式案例答案；数值只用于演示接口。 -->

# Methods and Experiments

## Methods

### Step sequence

- Load the CH4-sim dataset (v2.0) and split it into train:val:test = 70:15:15. Source: synthetic-config.yaml, dataset.
- Train the NiCo-200 model for 50 epochs with batch size 64, learning rate 0.0005, weight decay 0.00001 and the Adam optimizer, using hidden_dim 128 and 3 layers. Source: synthetic-config.yaml, model/training.
- Evaluate yield and stability on the test split with 5 repeats. Source: synthetic-config.yaml, evaluation.

### Experimental settings

Training used an NVIDIA RTX 4090 with fp16 precision, Python 3.11.4, PyTorch 2.2.0 and CUDA 12.4. The random seed was 7. Each evaluation condition was repeated 5 times.

### Datasets and splits

The CH4-sim dataset (v2.0, 8000 samples) was divided into train, validation and test subsets at 70:15:15. No additional preprocessing was reported, so the test split is described exactly as configured.

### Baselines and controls

The supplied materials contain no baseline system, version or shared-budget comparison conditions. No comparative claim is made; the baseline choice is recorded as gap G1.

### Reproducibility

Environment: Python 3.11.4, PyTorch 2.2.0, CUDA 12.4, NVIDIA RTX 4090, fp16. Command: `python code.py --data data.csv --seed 7 --epochs 50 --batch-size 64 --lr 0.0005`. Seed: 7. Data access: CH4-sim v2.0 as defined in synthetic-config.yaml. Hyperparameters were taken from synthetic-config.yaml.

### Limitations

The supplied config covers a single seed and one 70:15:15 split, so run-to-run variance across seeds and alternative splits cannot be assessed. Differences smaller than seed-to-seed variance, or conclusions under a different split, are unsupported by this configuration.

## Questions for the author

- Which baseline system and version, under which shared budget, should be used for comparison?
- Was the checkpoint selected on the validation split, and by which metric?
