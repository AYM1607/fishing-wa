{
  description = "Washington State fishing map — WDFW crawler + static Leaflet site";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            uv        # Python env + deps for the crawler
            nodejs_22 # JS runtime for the frontend
            pnpm      # JS package manager
            sqlite    # CLI to inspect the crawl database
          ];

          # uv manages its own Python; keep it from trying to download one.
          env.UV_PYTHON_PREFERENCE = "managed";

          shellHook = ''
            echo "fishing-wa dev shell — uv $(uv --version | cut -d' ' -f2), node $(node --version), pnpm $(pnpm --version)"
            echo "  crawl:  cd crawler && uv run crawl.py && uv run export_geojson.py"
            echo "  serve:  cd web && pnpm install && pnpm dev"
          '';
        };
      });
}
