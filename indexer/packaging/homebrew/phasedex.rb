# Homebrew formula for phasedex, phasectl's native C++ code indexer
# (docs/internal/NATIVE_INDEXER_PLAN.md, issue #3). No tap repo exists yet
# (a real tap needs its own `homebrew-<name>` repo, or a release tag on
# phasectl to point a stable `url`/`sha256` at) — this formula is a HEAD-only
# build off the working branch for now, usable directly:
#
#   brew install --HEAD --build-from-source indexer/packaging/homebrew/phasedex.rb
#
# Once native-cpp-indexer merges to main and M9 flips it to the default
# indexer, retarget the `head` url's branch to "main" and, eventually,
# replace `head` with a tagged `url`/`sha256` for a real stable formula.
class Phasedex < Formula
  desc "Native C++ code indexer for phasectl — no Node, no npm, no interpreter runtime"
  homepage "https://github.com/praveenprk/phasectl"
  license "MIT"
  head "https://github.com/praveenprk/phasectl.git", branch: "native-cpp-indexer"

  depends_on "cmake" => :build
  depends_on "sqlite3"

  def install
    system "cmake", "-S", "indexer", "-B", "build", *std_cmake_args
    system "cmake", "--build", "build"
    system "cmake", "--install", "build"
  end

  test do
    system bin/"phasedex", "index", testpath.to_s
    output = shell_output("#{bin}/phasedex query nonexistent --path #{testpath} --json")
    assert_match "[]", output
  end
end
