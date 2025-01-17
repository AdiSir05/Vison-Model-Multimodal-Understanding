import torch
import torch.nn as nn
import math
import torch.nn.functional as F
from collections import OrderedDict

"""
Vision Transformer model. This model is based of the Vision Transformer architecture from CLIP.
It is altered to be able to capture the fine detail of embedded watermarks.
"""


class QuickGELU(nn.Module):
    def forward(self, x: torch.Tensor):
        return x * torch.sigmoid(1.702 * x)



class ResidualAttentionBlock(nn.Module):
    def __init__(self, d_model: int, n_head: int, attn_mask: torch.Tensor = None):
        super().__init__()
        self.attn = nn.MultiheadAttention(d_model, n_head)
        self.ln_1 = nn.LayerNorm(d_model)
        self.mlp = nn.Sequential(OrderedDict([
            ("c_fc", nn.Linear(d_model, d_model * 4)),
            ("gelu", QuickGELU()),
            ("c_proj", nn.Linear(d_model * 4, d_model))
        ]))
        self.ln_2 = nn.LayerNorm(d_model)
        self.attn_mask = attn_mask

    def attention(self, x: torch.Tensor):
        self.attn_mask = self.attn_mask.to(dtype=x.dtype,
                                           device=x.device) if self.attn_mask is not None else None
        return self.attn(x, x, x, need_weights=False, attn_mask=self.attn_mask)[0]

    def forward(self, x: torch.Tensor):
        x = x + self.attention(self.ln_1(x))
        x = x + self.mlp(self.ln_2(x))
        return x


class Transformer(nn.Module):
    def __init__(self, width: int, layers: int, heads: int, attn_mask: torch.Tensor = None):
        super().__init__()
        self.width = width
        self.layers = layers
        self.resblocks = nn.Sequential(
            *[ResidualAttentionBlock(width, heads, attn_mask) for _ in range(layers)])

    def forward(self, x: torch.Tensor):
        return self.resblocks(x)


class VisionTransformerClassifier(nn.Module):
    def __init__(self, input_resolution: int, layers: int, heads: int, output_dim: int):
        super().__init__()
        self.input_resolution = input_resolution
        self.output_dim = output_dim
        self.patch_size = 3
        self.width = 64
        self.sample_ratio = 2
        self.conv1 = nn.Conv2d(in_channels=3, out_channels=32, padding=1, stride=2,
                               kernel_size=self.patch_size)
        self.conv2 = nn.Conv2d(in_channels=32, out_channels=64, padding=1, stride=4,
                               kernel_size=self.patch_size)
        self.dproj = nn.Conv2d(in_channels=64, out_channels=64, padding=1, stride=1,
                               kernel_size=self.patch_size, groups=64)
        self.pproj = nn.Conv2d(in_channels=64, out_channels=64, kernel_size=1)
        scale = self.width ** -0.5
        self.ln_pre = nn.LayerNorm(self.width)

        self.transformer = Transformer(self.width, layers, heads)

        self.ln_post = nn.LayerNorm(self.width)
        self.proj = nn.Parameter(scale * torch.randn(64, output_dim))

    def forward(self, x: torch.Tensor):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.dproj(x))
        x = F.relu(self.pproj(x))
        x = x.reshape(x.shape[0], x.shape[1], -1)  # shape = [*, width, grid ** 2]
        x = x.permute(0, 2, 1)  # shape = [*, grid ** 2, width]
        x = self.ln_pre(x)
        x = x.permute(1, 0, 2)  # NLD -> LND
        x = self.transformer(x)
        x = x.permute(1, 0, 2)  # LND -> NLD
        x = self.ln_post(x[:, 0, :])
        if self.proj is not None:
            x = x @ self.proj
        return x