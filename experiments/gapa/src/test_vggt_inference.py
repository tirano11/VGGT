#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

"""
VGGT 추론 테스트 스크립트

이 스크립트는:
1. VGGT 모델을 로드
2. 이미지에서 추론 실행
3. 예측 결과 분석
4. pose_enc를 extrinsic/intrinsic으로 변환
5. 결과를 npz 파일로 저장
"""

import os
import sys
import torch
import numpy as np
from pathlib import Path
from typing import Dict, Tuple, Optional

# Add parent directories to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

try:
    from vggt.models.vggt import VGGT
    from vggt.utils.load_fn import load_and_preprocess_images
    from vggt.utils.pose_enc import pose_encoding_to_extri_intri
except ImportError as e:
    print(f"❌ 임포트 오류: {e}")
    print("VGGT 패키지가 설치되어 있는지 확인해주세요.")
    sys.exit(1)


class VGGTInferenceTest:
    """VGGT 추론 테스트 클래스"""
    
    def __init__(self, output_dir: str = "outputs/scene_001"):
        """
        초기화
        
        Args:
            output_dir: 결과를 저장할 디렉토리
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 디바이스 설정
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"✓ 디바이스: {self.device}")
        
        # 데이터 타입 설정 (Ampere GPU에서 bfloat16 지원)
        if self.device == "cuda" and torch.cuda.get_device_capability()[0] >= 8:
            self.dtype = torch.bfloat16
        else:
            self.dtype = torch.float16
        print(f"✓ 데이터 타입: {self.dtype}")
        
        self.model = None
        self.predictions = None
    
    def load_model(self) -> bool:
        """
        VGGT 모델 로드
        
        Returns:
            성공 여부
        """
        try:
            print("\n📦 VGGT 모델 로드 중...")
            self.model = VGGT.from_pretrained("facebook/VGGT-1B")
            self.model.to(self.device)
            self.model.eval()
            print("✓ VGGT 모델 로드 완료")
            return True
        except Exception as e:
            print(f"❌ 모델 로드 실패: {e}")
            return False
    
    def load_images(self, image_dir: str) -> Optional[torch.Tensor]:
        """
        이미지 디렉토리에서 모든 이미지 로드
        
        Args:
            image_dir: 이미지가 있는 디렉토리 경로
        
        Returns:
            로드된 이미지 텐서 또는 None
        """
        image_dir = Path(image_dir)
        
        if not image_dir.exists():
            print(f"❌ 이미지 디렉토리를 찾을 수 없습니다: {image_dir}")
            return None
        
        # 지원하는 이미지 형식
        image_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".gif"}
        image_paths = sorted([
            str(p) for p in image_dir.iterdir()
            if p.suffix.lower() in image_extensions
        ])
        
        if not image_paths:
            print(f"❌ 이미지를 찾을 수 없습니다: {image_dir}")
            print(f"   지원하는 형식: {', '.join(image_extensions)}")
            return None
        
        try:
            print(f"\n📷 이미지 로드 중 ({len(image_paths)}개)...")
            for i, path in enumerate(image_paths):
                print(f"   {i+1}. {Path(path).name}")
            
            images = load_and_preprocess_images(image_paths)
            images = images.to(self.device)
            print(f"✓ 이미지 로드 완료 - Shape: {images.shape}")
            return images
        except Exception as e:
            print(f"❌ 이미지 로드 실패: {e}")
            return None
    
    def run_inference(self, images: torch.Tensor) -> bool:
        """
        VGGT 추론 실행
        
        Args:
            images: 입력 이미지 텐서
        
        Returns:
            성공 여부
        """
        if self.model is None:
            print("❌ 모델이 로드되지 않았습니다.")
            return False
        
        try:
            print("\n🚀 VGGT 추론 실행 중...")
            
            with torch.no_grad():
                with torch.cuda.amp.autocast(dtype=self.dtype):
                    # 배치 차원 추가
                    images_batch = images.unsqueeze(0) if images.dim() == 3 else images
                    
                    # 모델 실행
                    aggregated_tokens_list, ps_idx = self.model.aggregator(images_batch)
                    
                    # 각 헤드에서 예측
                    pose_enc_pred = self.model.camera_head(aggregated_tokens_list)[-1]
                    depth_pred = self.model.dpt_head(aggregated_tokens_list)[-1]
                    point_map_pred = self.model.track_head(aggregated_tokens_list)[-1]
            
            # 결과 저장
            self.predictions = {
                "pose_enc": pose_enc_pred,
                "depth": depth_pred,
                "point_maps": point_map_pred,
                "aggregated_tokens": aggregated_tokens_list,
            }
            
            print("✓ 추론 완료")
            return True
        except Exception as e:
            print(f"❌ 추론 실패: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def print_predictions(self) -> None:
        """예측 결과 출력"""
        if self.predictions is None:
            print("❌ 예측 결과가 없습니다.")
            return
        
        print("\n📊 예측 결과:")
        print(f"예측 키: {list(self.predictions.keys())}")
        
        print("\n각 예측의 형태:")
        for key, value in self.predictions.items():
            if isinstance(value, torch.Tensor):
                print(f"  {key}: {value.shape} - dtype: {value.dtype}")
            elif isinstance(value, list):
                print(f"  {key}: list (길이: {len(value)})")
                if value and isinstance(value[0], torch.Tensor):
                    print(f"    첫 번째 요소: {value[0].shape}")
            else:
                print(f"  {key}: {type(value)}")
    
    def convert_pose_encoding(self) -> bool:
        """
        pose_enc를 extrinsic과 intrinsic으로 변환
        
        Returns:
            성공 여부
        """
        if self.predictions is None or "pose_enc" not in self.predictions:
            print("⚠️  pose_enc를 찾을 수 없습니다.")
            return False
        
        try:
            print("\n🔄 pose_enc를 extrinsic/intrinsic으로 변환 중...")
            
            pose_enc = self.predictions["pose_enc"]
            extri, intri = pose_encoding_to_extri_intri(pose_enc)
            
            # 결과 저장
            self.predictions["extrinsics"] = extri
            self.predictions["intrinsics"] = intri
            
            print(f"✓ Extrinsic shape: {extri.shape}")
            print(f"✓ Intrinsic shape: {intri.shape}")
            
            return True
        except Exception as e:
            print(f"❌ pose_enc 변환 실패: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def save_predictions_npz(self) -> bool:
        """
        예측 결과를 NPZ 파일로 저장
        
        Returns:
            성공 여부
        """
        if self.predictions is None:
            print("❌ 저장할 예측 결과가 없습니다.")
            return False
        
        try:
            print("\n💾 결과를 NPZ 파일로 저장 중...")
            
            # 텐서를 numpy로 변환
            predictions_np = {}
            for key, value in self.predictions.items():
                if isinstance(value, torch.Tensor):
                    predictions_np[key] = value.cpu().numpy()
                elif isinstance(value, list):
                    # 리스트의 경우 배열로 변환 시도
                    try:
                        predictions_np[key] = np.array([v.cpu().numpy() if isinstance(v, torch.Tensor) else v 
                                                       for v in value], dtype=object)
                    except:
                        print(f"⚠️  {key}는 저장 불가능 (복잡한 리스트 구조)")
                else:
                    try:
                        predictions_np[key] = np.array(value)
                    except:
                        print(f"⚠️  {key}는 저장 불가능")
            
            # NPZ 파일로 저장
            output_path = self.output_dir / "debug_predictions.npz"
            np.savez_compressed(str(output_path), **predictions_np)
            
            print(f"✓ NPZ 파일 저장 완료: {output_path}")
            print(f"  저장된 키: {list(predictions_np.keys())}")
            
            return True
        except Exception as e:
            print(f"❌ 저장 실패: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def run_full_pipeline(self, image_dir: str) -> bool:
        """
        전체 파이프라인 실행
        
        Args:
            image_dir: 이미지 디렉토리 경로
        
        Returns:
            성공 여부
        """
        print("=" * 60)
        print("VGGT 추론 테스트 시작")
        print("=" * 60)
        
        # 1. 모델 로드
        if not self.load_model():
            return False
        
        # 2. 이미지 로드
        images = self.load_images(image_dir)
        if images is None:
            return False
        
        # 3. 추론 실행
        if not self.run_inference(images):
            return False
        
        # 4. 결과 출력
        self.print_predictions()
        
        # 5. pose_enc 변환
        self.convert_pose_encoding()
        
        # 6. 결과 저장
        if not self.save_predictions_npz():
            return False
        
        print("\n" + "=" * 60)
        print("✓ VGGT 추론 테스트 완료!")
        print("=" * 60)
        
        return True


def main():
    """메인 함수"""
    
    # 이미지 디렉토리 (상대 경로 사용)
    image_dir = "experiments/gapa/data/scene_001/images"
    output_dir = "outputs/scene_001"
    
    # 절대 경로로 변환 (프로젝트 루트 기준)
    project_root = Path(__file__).parent.parent.parent.parent
    image_dir = project_root / image_dir
    output_dir = project_root / output_dir
    
    print(f"프로젝트 루트: {project_root}")
    print(f"이미지 디렉토리: {image_dir}")
    print(f"출력 디렉토리: {output_dir}")
    
    # 테스트 실행
    tester = VGGTInferenceTest(output_dir=str(output_dir))
    success = tester.run_full_pipeline(str(image_dir))
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
