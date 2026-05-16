#!/usr/bin/env python3
"""
Auto-generate registry.json by scanning plugins directory
"""
import json
import os
from datetime import datetime

def generate_registry():
    plugins_dir = "plugins"
    registry = {
        "generated_at": datetime.utcnow().isoformat(),
        "plugins": []
    }
    
    if not os.path.exists(plugins_dir):
        print("No plugins directory found")
        return
    
    for item in os.listdir(plugins_dir):
        item_path = os.path.join(plugins_dir, item)
        
        if not os.path.isdir(item_path):
            continue
        
        manifest_path = os.path.join(item_path, "manifest.json")
        
        if os.path.exists(manifest_path):
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
            
            # Verify required fields
            required = ["id", "name", "version", "description"]
            if all(k in manifest for k in required):
                registry["plugins"].append(manifest)
                print(f"Added: {manifest['name']}")
            else:
                print(f"Skipped {item}: Missing required fields")
    
    registry["total"] = len(registry["plugins"])
    
    with open("registry.json", "w") as f:
        json.dump(registry, f, indent=2)
    
    print(f"\nRegistry generated with {registry['total']} plugins")

if __name__ == "__main__":
    generate_registry()
