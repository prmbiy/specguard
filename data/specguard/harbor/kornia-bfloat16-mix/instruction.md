Support bfloat16 tensors in mix augmentations, including RandomMixUpV2, without rejecting the dtype. Preserve input dtype and device. With mixing probability zero, image and class data pass through unchanged. Inputs are CPU bfloat16 image tensors and class-label tensors plus augmentation probability and data keys; observables are returned images, labels, dtype, device, and whether the operation raises. Preserve existing float32/float64 behavior.
Satisfy the existing checks associated with tests/augmentation/test_augmentation_mix.py::TestRandomMixUpV2::test_random_mixup_p0 without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: seed the tensor RNG with 0; call RandomMixUpV2 with p=0.0 and data_keys=["input", "class"]. The image batch stacks an all-ones and an all-zeros tensor, each of shape (1, 3, 4), and the input label vector is [1, 0]. Device and dtype come from fixtures.
- Observations: the returned image tensor and the third column of the returned label tensor, after the augmentation call, along with any exception. The selected method is TestRandomMixUpV2.test_random_mixup_p0, not the similarly named CutMix method.
- Output shape: a pair of tensors; keep image shape, dtype/device, and values, and label shape and column values available independently. The observed label output has an indexable second dimension; it is not merely the original one-dimensional class vector.
