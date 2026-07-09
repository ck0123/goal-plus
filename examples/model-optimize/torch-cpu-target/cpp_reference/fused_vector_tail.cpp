#include <torch/extension.h>

#include <algorithm>
#include <cmath>

torch::Tensor fused_vector_tail(torch::Tensor hidden) {
  auto input = hidden.contiguous();
  auto output = torch::empty_like(input);
  const auto n = input.numel();
  const float* in = input.data_ptr<float>();
  float* out = output.data_ptr<float>();

  for (int64_t i = 0; i < n; ++i) {
    const float a = in[i] * 1.03125f + 0.125f;
    const float b = a > 0.0f ? a : 0.0f;
    const float c = b * b;
    const float d = c + b * 0.5f;
    const float e = std::sqrt(std::max(d + 0.001f, 0.0f));
    out[i] = e * 0.75f + b * 0.25f;
  }
  return output;
}

TORCH_LIBRARY(cpu_torch_vector_opt, m) {
  m.def("fused_vector_tail(Tensor hidden) -> Tensor");
}

TORCH_LIBRARY_IMPL(cpu_torch_vector_opt, CPU, m) {
  m.impl("fused_vector_tail", fused_vector_tail);
}
