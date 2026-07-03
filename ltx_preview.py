"""Cheap latent->RGB live preview for LTX 0.9.x (diffusers).

The packed callback latent is [B, S, 128]; with transformer patch_size=patch_size_t=1
(LTX 0.9.x) it unpacks to [B,128,LF,LH,LW] via a plain reshape. We project the 128
latent channels of one temporal slice to RGB with ComfyUI's published LTXV
latent_rgb_factors (128x3) + bias. No VAE, no extra VRAM, ~1e5 MACs -> effectively free.
Everything is best-effort: any failure is swallowed so generation never dies.
"""
import os
import torch
from PIL import Image

# ComfyUI comfy/latent_formats.py class LTXV -- applies to RAW latents (no denorm).
LATENT_RGB_FACTORS = [
    [ 1.1202e-02,-6.3815e-04,-1.0021e-02],[ 8.6031e-02, 6.5813e-02, 9.5409e-04],[-1.2576e-02,-7.5734e-03,-4.0528e-03],[ 9.4063e-03,-2.1688e-03, 2.6093e-03],
    [ 3.7636e-03, 1.2765e-02, 9.1548e-03],[ 2.1024e-02,-5.2973e-03, 3.4373e-03],[-8.8896e-03,-1.9703e-02,-1.8761e-02],[-1.3160e-02,-1.0523e-02, 1.9709e-03],
    [-1.5152e-03,-6.9891e-03,-7.5810e-03],[-1.7247e-03, 4.6560e-04,-3.3839e-03],[ 1.3617e-02, 4.7077e-03,-2.0045e-03],[ 1.0256e-02, 7.7318e-03, 1.3948e-02],
    [-1.6108e-02,-6.2151e-03, 1.1561e-03],[ 7.3407e-03, 1.5628e-02, 4.4865e-04],[ 9.5357e-04,-2.9518e-03,-1.4760e-02],[ 1.9143e-02, 1.0868e-02, 1.2264e-02],
    [ 4.4575e-03, 3.6682e-05,-6.8508e-03],[-4.5681e-04, 3.2570e-03, 7.7929e-03],[ 3.3902e-02, 3.3405e-02, 3.7454e-02],[-2.3001e-02,-2.4877e-03,-3.1033e-03],
    [ 5.0265e-02, 3.8841e-02, 3.3539e-02],[-4.1018e-03,-1.1095e-03, 1.5859e-03],[-1.2689e-01,-1.3107e-01,-2.1005e-01],[ 2.6276e-02, 1.4189e-02,-3.5963e-03],
    [-4.8679e-03, 8.8486e-03, 7.8029e-03],[-1.6610e-03,-4.8597e-03,-5.2060e-03],[-2.1010e-03, 2.3610e-03, 9.3796e-03],[-2.2482e-02,-2.1305e-02,-1.5087e-02],
    [-1.5753e-02,-1.0646e-02,-6.5083e-03],[-4.6975e-03, 5.0288e-03,-6.7390e-03],[ 1.1951e-02, 2.0712e-02, 1.6191e-02],[-6.3704e-03,-8.4827e-03,-9.5483e-03],
    [ 7.2610e-03,-9.9326e-03,-2.2978e-02],[-9.1904e-04, 6.2882e-03, 9.5720e-03],[-3.7178e-02,-3.7123e-02,-5.6713e-02],[-1.3373e-01,-1.0720e-01,-5.3801e-02],
    [-5.3702e-03, 8.1256e-03, 8.8397e-03],[-1.5247e-01,-2.1437e-01,-2.1843e-01],[ 3.1441e-02, 7.0335e-03,-9.7541e-03],[ 2.1528e-03,-8.9817e-03,-2.1023e-02],
    [ 3.8461e-03,-5.8957e-03,-1.5014e-02],[-4.3470e-03,-1.2940e-02,-1.5972e-02],[-5.4781e-03,-1.0842e-02,-3.0204e-03],[-6.5347e-03, 3.0806e-03,-1.0163e-02],
    [-5.0414e-03,-7.1503e-03,-8.9686e-04],[-8.5851e-03,-2.4351e-03, 1.0674e-03],[-9.0016e-03,-9.6493e-03, 1.5692e-03],[ 5.0914e-03, 1.2099e-02, 1.9968e-02],
    [ 1.3758e-02, 1.1669e-02, 8.1958e-03],[-1.0518e-02,-1.1575e-02,-4.1307e-03],[-2.8410e-02,-3.1266e-02,-2.2149e-02],[ 2.9336e-03, 3.6511e-02, 1.8717e-02],
    [-1.6703e-02,-1.6696e-02,-4.4529e-03],[ 4.8818e-02, 4.0063e-02, 8.7410e-03],[-1.5066e-02,-5.7328e-04, 2.9785e-03],[-1.7613e-02,-8.1034e-03, 1.3086e-02],
    [-9.2633e-03, 1.0803e-02,-6.3489e-03],[ 3.0851e-03, 4.7750e-04, 1.2347e-02],[-2.2785e-02,-2.3043e-02,-2.6005e-02],[-2.4787e-02,-1.5389e-02,-2.2104e-02],
    [-2.3572e-02, 1.0544e-03, 1.2361e-02],[-7.8915e-03,-1.2271e-03,-6.0968e-03],[-1.1478e-02,-1.2543e-03, 6.2679e-03],[-5.4229e-02, 2.6644e-02, 6.3394e-03],
    [ 4.4216e-03,-7.3338e-03,-1.0464e-02],[-4.5013e-03, 1.6082e-03, 1.4420e-02],[ 1.3673e-02, 8.8877e-03, 4.1253e-03],[-1.0145e-02, 9.0072e-03, 1.5695e-02],
    [-5.6234e-03, 1.1847e-03, 8.1261e-03],[-3.7171e-03,-5.3538e-03, 1.2590e-03],[ 2.9476e-02, 2.1424e-02, 3.0424e-02],[-3.4925e-02,-2.4340e-02,-2.5316e-02],
    [-3.4127e-02,-2.2406e-02,-1.0589e-02],[-1.7342e-02,-1.3249e-02,-1.0719e-02],[-2.1478e-03,-8.6051e-03,-2.9878e-03],[ 1.2089e-03,-4.2391e-03,-6.8569e-03],
    [ 9.0411e-04,-6.6886e-03,-6.7547e-05],[ 1.6048e-02,-1.0057e-02,-2.8929e-02],[ 1.2290e-03, 1.0163e-02, 1.8861e-02],[ 1.7264e-02, 2.7257e-04, 1.3785e-02],
    [-1.3482e-02,-3.6427e-03, 6.7481e-04],[ 4.6782e-03,-5.2423e-03, 2.4467e-03],[-5.9113e-03,-6.2244e-03,-1.8162e-03],[ 1.5496e-02, 1.4582e-02, 1.9514e-03],
    [ 7.4958e-03, 1.5886e-03,-8.2305e-03],[ 1.9086e-02, 1.6360e-03,-3.9674e-03],[-5.7021e-03,-2.7307e-03,-4.1066e-03],[ 1.7450e-03, 1.4602e-02, 2.5794e-02],
    [-8.2788e-04, 2.2902e-03, 4.5161e-03],[ 1.1632e-02, 8.9193e-03,-7.2813e-03],[ 7.5721e-03, 2.6784e-03, 1.1393e-02],[ 5.1939e-03, 3.6903e-03, 1.4049e-02],
    [-1.8383e-02,-2.2529e-02,-2.4477e-02],[ 5.8842e-04,-5.7874e-03,-1.4770e-02],[-1.6125e-02,-8.6101e-03,-1.4533e-02],[ 2.0540e-02, 2.0729e-02, 6.4338e-03],
    [ 3.3587e-03,-1.1226e-02,-1.6444e-02],[-1.4742e-03,-1.0489e-02, 1.7097e-03],[ 2.8130e-02, 2.3546e-02, 3.2791e-02],[-1.8532e-02,-1.2842e-02,-8.7756e-03],
    [-8.0533e-03,-1.0771e-02,-1.7536e-02],[-3.9009e-03, 1.6150e-02, 3.3359e-02],[-7.4554e-03,-1.4154e-02,-6.1910e-03],[ 3.4734e-03,-1.1370e-02,-1.0581e-02],
    [ 1.1476e-02, 3.9281e-03, 2.8231e-03],[ 7.1639e-03,-1.4741e-03,-3.8066e-03],[ 2.2250e-03,-8.7552e-03,-9.5719e-03],[ 2.4146e-02, 2.1696e-02, 2.8056e-02],
    [-5.4365e-03,-2.4291e-02,-1.7802e-02],[ 7.4263e-03, 1.0510e-02, 1.2705e-02],[ 6.2669e-03, 6.2658e-03, 1.9211e-02],[ 1.6378e-02, 9.4933e-03, 6.6971e-03],
    [ 1.7173e-02, 2.3601e-02, 2.3296e-02],[-1.4568e-02,-9.8279e-03,-1.1556e-02],[ 1.4431e-02, 1.4430e-02, 6.6362e-03],[-6.8230e-03, 1.8863e-02, 1.4555e-02],
    [ 6.1156e-03, 3.4700e-03,-2.6662e-03],[-2.6983e-03,-5.9402e-03,-9.2276e-03],[ 1.0235e-02, 7.4173e-03,-7.6243e-03],[-1.3255e-02, 1.9322e-02,-9.2153e-04],
    [ 2.4222e-03,-4.8039e-03,-1.5759e-02],[ 2.6244e-02, 2.5951e-02, 2.0249e-02],[ 1.5711e-02, 1.8498e-02, 2.7407e-03],[-2.1714e-03, 4.7214e-03,-2.2443e-02],
    [-7.4747e-03, 7.4166e-03, 1.4430e-02],[-8.3906e-03,-7.9776e-03, 9.7927e-03],[ 3.8321e-02, 9.6622e-03,-1.9268e-02],[-1.4605e-02,-6.7032e-03, 3.9675e-03],
]
LATENT_RGB_BIAS = [-0.0571, -0.1657, -0.2512]

# Module-level cache of the projection matrices (built once on first use).
_RGB_W = None   # [3,128]
_RGB_B = None   # [3]


def _factors(device, dtype=torch.float32):
    global _RGB_W, _RGB_B
    if _RGB_W is None:
        _RGB_W = torch.tensor(LATENT_RGB_FACTORS).transpose(0, 1).contiguous()  # [3,128]
        _RGB_B = torch.tensor(LATENT_RGB_BIAS)                                  # [3]
    return _RGB_W.to(device, dtype), _RGB_B.to(device, dtype)


def latent_grid_dims(width, height, num_frames):
    """Latent grid (LF, LH, LW) for LTX: spatial /32, temporal /8 (+1)."""
    lh = height // 32
    lw = width // 32
    lf = (num_frames - 1) // 8 + 1
    return lf, lh, lw


def latent_to_preview_image(packed_latents, lf, lh, lw, frame=-1, out_width=160):
    """packed_latents: [B, S, 128] (S == lf*lh*lw). Returns a small RGB PIL.Image, or None.

    Unpack (patch sizes = 1 for LTX 0.9.x) -> [B,128,LF,LH,LW]; project one temporal
    slice's 128 channels to RGB; clamp 0..1; upscale NEAREST to out_width. Never raises.
    """
    try:
        lat = packed_latents
        if lat is None:
            return None
        if lat.dim() == 3:
            # PACKED [B, S, C] -> [B, C, LF, LH, LW]
            b, s, c = lat.shape
            if s != lf * lh * lw:
                return None
            u = lat.reshape(b, lf, lh, lw, c).permute(0, 4, 1, 2, 3)
        elif lat.dim() == 5:
            u = lat  # already [B,C,LF,LH,LW] (defensive; not the callback path)
        else:
            return None
        x0 = u[0, :, frame].float()                      # [128, LH, LW]
        w, bias = _factors(x0.device, torch.float32)     # [3,128], [3]
        rgb = torch.nn.functional.linear(x0.movedim(0, -1), w, bias=bias)  # [LH,LW,3]
        # The raw projection is low/compressed -> a plain clamp(0,1) looks dark & muddy.
        # Contrast-stretch the 2nd..98th percentile (global, so colour balance is kept) to 0..1
        # for a punchy, discernible live preview. The decoded final frame skips this path.
        flat = rgb.flatten()
        lo = torch.quantile(flat, 0.02)
        hi = torch.quantile(flat, 0.98)
        rgb = ((rgb - lo) / (hi - lo + 1e-5)).clamp(0, 1)
        rgb = rgb.mul(255).round().to(torch.uint8).cpu().numpy()
        img = Image.fromarray(rgb)                        # tiny, e.g. 22x15
        h = max(1, round(img.height * out_width / img.width))
        return img.resize((out_width, h), Image.NEAREST)
    except Exception:
        return None


def wan_latent_preview(latents, frame=-1, out_width=160):
    """Cheap rough preview of a Wan VAE latent [B,C,F,H,W] (no VAE decode -> safe on 8GB). Projects the
    first 3 latent channels of one temporal slice with a global percentile contrast-stretch. Color is
    APPROXIMATE (latent space != RGB) -> a live denoising-progress view, not the final frame. Never raises."""
    try:
        lat = latents
        if lat is None or lat.dim() != 5:
            return None
        x = lat[0, :, frame].float()                 # [C, H, W]
        rgb = x[:3] if x.shape[0] >= 3 else x[:1].repeat(3, 1, 1)
        rgb = rgb.movedim(0, -1)                      # [H, W, 3]
        flat = rgb.flatten()
        lo, hi = torch.quantile(flat, 0.02), torch.quantile(flat, 0.98)
        rgb = ((rgb - lo) / (hi - lo + 1e-5)).clamp(0, 1)
        rgb = rgb.mul(255).round().to(torch.uint8).cpu().numpy()
        img = Image.fromarray(rgb)
        h = max(1, round(img.height * out_width / img.width))
        return img.resize((out_width, h), Image.NEAREST)
    except Exception:
        return None


def atomic_save_png(img, path):
    """Write img to path atomically (tmp in same dir + os.replace). Never raises."""
    if img is None or not path:
        return False
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        img.save(tmp, "PNG")
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False


def write_preview_from_latents(packed_latents, args_or_path, lf, lh, lw, frame=-1):
    """Convenience: latent -> tiny RGB -> atomic PNG at path. Returns True on success."""
    path = args_or_path if isinstance(args_or_path, str) else getattr(args_or_path, "preview", None)
    if not path:
        return False
    img = latent_to_preview_image(packed_latents, lf, lh, lw, frame=frame)
    return atomic_save_png(img, path)
