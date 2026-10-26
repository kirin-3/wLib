{
  description = "wLib - Linux game manager for F95Zone";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in
    {
      packages.${system} = {
        wlib = pkgs.callPackage ./nix/package.nix { };
        default = self.packages.${system}.wlib;
      };

      overlays.default = final: _prev: {
        wlib = final.callPackage ./nix/package.nix { };
      };
    };
}
