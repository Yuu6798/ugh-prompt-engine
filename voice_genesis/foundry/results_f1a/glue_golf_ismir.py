"""VG-F1a 経路B: GOLF ISMIR23 歌声 checkpoint を、LightningCLI/jsonargparse を経由せず
config.yaml から直接モデルを再構築し、共通 f0/amp（f1a_control.npz）で明示駆動する。

背景（生ログ参照）: `autoencode.py predict` は ISMIR23 config が現行 VoiceAutoEncoder
の __init__ シグネチャに存在しないキー（feature_trsfm/hop_length/l1_loss_weight/window）
を含むため jsonargparse で拒否される。さらに現行 `VoiceAutoEncoder.forward()` は
`self.encoder(x, f0=f0)` と生波形+f0 を encoder に渡すが、ISMIR23 の
backbone (`models.mel.Mel2Control`) は `forward(self, mels)` のみを受け付け f0 引数が
無い（学習時は外側の feature_trsfm で波形→mel変換してから encoder に渡していたと
推定される、現行コードはその変換ステップ自体を消失させている）。
→ 本スクリプトは config.yaml を直接読み `class_path`/`init_args` を再帰的に
instantiate し、feature_trsfm 相当の mel 変換を自前で復元して補う（/workspace 非改変、
本スクリプトのみで完結）。明示 f0 は `predict_step` と同じ機構（`params["phase"]` を
事前にセットして encoder 予測 f0 を上書き）で注入する。
"""
from __future__ import annotations

import sys
from importlib import import_module

import numpy as np
import soundfile as sf
import torch
import yaml

sys.path.insert(0, "/workspace/yoyololicon/golf")

from ltng.ae import VoiceAutoEncoder  # noqa: E402
from ltng.vocoder import ScaledLogMelSpectrogram  # noqa: E402
from models.audiotensor import AudioTensor  # noqa: E402

OUT = "/tmp/claude-0/-home-user-ugh-prompt-engine/1c025cfe-cb3e-592b-b577-d0be9640c799/scratchpad/foundry_f1a"
GOLF = "/workspace/yoyololicon/golf"


def instantiate(obj):
    """config.yaml の class_path/init_args ノードを再帰的に実インスタンス化する
    （jsonargparse の代替。現行クラスの __init__ シグネチャと厳密照合しないため
    バージョン差異（centred 等）に頑健）。"""
    if isinstance(obj, dict) and "class_path" in obj:
        module_path, cls_name = obj["class_path"].rsplit(".", 1)
        cls = getattr(import_module(module_path), cls_name)
        init_args = {k: instantiate(v) for k, v in obj.get("init_args", {}).items()}
        return cls(**init_args)
    if isinstance(obj, dict):
        return {k: instantiate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [instantiate(v) for v in obj]
    return obj


def load_model(ckpt_dir: str, ckpt_file: str):
    with open(f"{ckpt_dir}/config.yaml") as f:
        cfg = yaml.safe_load(f)
    mc = cfg["model"]
    import inspect

    accepted = set(inspect.signature(VoiceAutoEncoder.__init__).parameters.keys())
    kwargs = {k: instantiate(v) for k, v in mc.items() if k in accepted}
    model = VoiceAutoEncoder(**kwargs)

    feature_trsfm = ScaledLogMelSpectrogram(
        window=mc["window"],
        sample_rate=mc["sample_rate"],
        hop_length=mc["hop_length"],
        **mc["feature_trsfm"]["init_args"],
    )

    sd = torch.load(f"{ckpt_dir}/{ckpt_file}", map_location="cpu", weights_only=False)["state_dict"]
    missing, unexpected = model.load_state_dict(sd, strict=False)
    print(f"  load_state_dict: missing={len(missing)} unexpected={len(unexpected)}")
    model.eval()
    return model, feature_trsfm


def explicit_f0_forward(model, feature_trsfm, x: torch.Tensor, f0_hz: torch.Tensor):
    """predict_step 相当だが、encoder に f0 を渡さず（backbone が非対応のため）
    事前に params['phase'] をセットして予測 f0 を上書きする（明示 f0 駆動）。"""
    xa = AudioTensor(x)
    f0a = AudioTensor(f0_hz)
    phase = torch.where(f0a == 0, torch.tensor(150.0), f0a) / model.sample_rate

    with torch.no_grad():
        mel = feature_trsfm(xa)
        enc_params = model.encoder(mel)
        params = {"phase": phase}
        params.update(enc_params)
        params.pop("f0", None)
        voicing_logits = params.pop("voicing_logits", None)
        if voicing_logits is not None:
            params["voicing"] = torch.sigmoid(voicing_logits)
        y = model.decoder(**params)
    return y


def main() -> None:
    d = np.load(f"{OUT}/f1a_control.npz", allow_pickle=True)
    f0, sr = d["f0"], int(d["sr"][0])
    wav, wav_sr = sf.read(f"{OUT}/golf_ismir_input/sakura_001.wav")
    assert wav_sr == sr
    x = torch.from_numpy(wav.astype(np.float32)).view(1, -1)
    f0_t = torch.from_numpy(f0.astype(np.float32)).view(1, -1)
    n = min(x.shape[1], f0_t.shape[1])
    x, f0_t = x[:, :n], f0_t[:, :n]

    targets = [
        ("glottal_d_f1", "epoch=2669-step=792990_converted.ckpt", "f1a_golf_ismir23_glottal_d_f1.wav"),
        ("ddsp_f1", "epoch=749-step=445500-val_loss=3.132.ckpt", "f1a_golf_ismir23_ddsp_f1.wav"),
    ]
    for ckpt_name, ckpt_file, out_name in targets:
        ckpt_dir = f"{GOLF}/ckpts/ismir23/{ckpt_name}"
        print(f"=== {ckpt_name} ===")
        model, feature_trsfm = load_model(ckpt_dir, ckpt_file)
        y = explicit_f0_forward(model, feature_trsfm, x, f0_t)
        y = y.as_tensor().squeeze(0).detach().numpy() if hasattr(y, "as_tensor") else y.squeeze(0).detach().numpy()
        peak = np.abs(y).max()
        if peak > 0.99:
            y = y / peak * 0.9
        sf.write(f"{OUT}/{out_name}", y.astype(np.float32), sr)
        print(f"  saved {out_name} len={len(y)/sr:.3f}s peak={np.abs(y).max():.3f}")


if __name__ == "__main__":
    main()
