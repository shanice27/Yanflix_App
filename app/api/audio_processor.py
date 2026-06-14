import subprocess
from pathlib import Path


class YanflixAudioEngine:
    def __init__(self):
        # 1. Establish absolute baseline anchor paths
        self.root_dir = Path(__file__).parent.parent
        self.workspace_dir = self.root_dir / "workspace"
        
        # 2. Map out the production processing conveyor belt folders
        self.dirs = {
            "raw_videos": self.workspace_dir / "0_raw_videos",
            "inputs": self.workspace_dir / "1_inputs",
            "isolated": self.workspace_dir / "2_isolated",
            "transcripts": self.workspace_dir / "3_transcripts",
            "projects": self.workspace_dir / "4_projects",
            "outputs": self.workspace_dir / "5_outputs",
            "cache": self.workspace_dir / "4_cloned_cached",
        }
        
        # 3. Automatically generate them if they don't exist yet
        for folder_path in self.dirs.values():
            folder_path.mkdir(parents=True, exist_ok=True)

    def separate_audio(self, input_audio_path: Path) -> dict:
        """
        Invokes Demucs to separate vocals from background SFX/Music matrix tracks,
        storing them cleanly inside the structured isolated folder.
        """
        # Create a clean folder inside 2_isolated specifically for this track file
        track_output_dir = self.dirs["isolated"] / input_audio_path.stem
        track_output_dir.mkdir(parents=True, exist_ok=True)
        
        print("🤖 Demucs Isolation Matrix Active: Splitting audio tracks...")
        
        # Execute htdemucs separation engine parameters
        command = [
            "demucs",
            "--two-stems", "vocals",
            "-o", str(track_output_dir),
            str(input_audio_path)
        ]
        
        # Run separation thread safely
        subprocess.run(command, capture_output=True, text=True, check=True)
        
        # Locate the structural paths generated natively by Demucs
        demucs_built_path = track_output_dir / "htdemucs" / input_audio_path.stem
        vocals_path = demucs_built_path / "vocals.wav"
        background_path = demucs_built_path / "no_vocals.wav"
        
        if vocals_path.exists() and background_path.exists():
            return {
                "vocals": vocals_path,
                "background": background_path
            }
        else:
            raise FileNotFoundError("Demucs completed, but folder stem validation failed.")