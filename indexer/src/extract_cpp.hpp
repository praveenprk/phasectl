// Naive C++ symbol extraction (M4, docs/internal/NATIVE_INDEXER_PLAN.md).
//
// Regex/line-based, not an AST: no preprocessor handling, no cross-file
// resolution, single-line signatures only (a declaration whose `(args)`
// or `{` spans multiple lines is missed). That's an explicit, accepted
// limitation for a first cut — see the plan's M4 entry. First target is
// the indexer's own C++ source (self-hosted dogfooding); other source
// languages come later (M7).
//
// Detects:
//   - class/struct declarations ending in `{` on the same line.
//   - function/method definitions: a `name(args) {` (optionally followed
//     by `const`/`noexcept` before the brace) whose head isn't a
//     control-flow keyword. `Qualifier::name(...)` is tagged as a method.
// Misses: multi-line signatures, operator overloads, templates spanning
// multiple lines, anything inside a string/char literal that happens to
// look like a declaration (literals aren't specially handled).

#pragma once

#include <regex>
#include <string>
#include <unordered_set>
#include <vector>

#include "lang_common.hpp"
#include "store.hpp"

namespace indexer {

namespace detail {

inline const std::unordered_set<std::string>& cpp_keywords() {
    static const std::unordered_set<std::string> kw = {
        "if",     "for",      "while",     "switch",  "catch",
        "return", "sizeof",   "new",       "delete",  "do",
        "else",   "using",    "namespace", "typedef", "template",
        "static_assert", "throw", "class", "struct",  "union",
        "enum",   "case",     "default",   "explicit","noexcept",
        "const",
    };
    return kw;
}

}  // namespace detail

inline std::vector<Symbol> extract_cpp_symbols(const std::string& file_path,
                                                const std::string& source) {
    std::vector<Symbol> symbols;

    const std::string cleaned = detail::strip_comments(source);
    const auto close_for_open_line = detail::match_braces(cleaned);

    static const std::regex class_re(
        R"(^\s*(class|struct)\s+([A-Za-z_]\w*)\b[^{;]*\{\s*$)");
    static const std::regex func_re(
        R"(^\s*(.+?)\(([^()]*)\)\s*(?:const)?\s*(?:noexcept)?\s*\{\s*$)");

    std::size_t pos = 0;
    int line_no = 0;
    while (pos <= cleaned.size()) {
        std::size_t nl = cleaned.find('\n', pos);
        const std::string line = (nl == std::string::npos)
                                      ? cleaned.substr(pos)
                                      : cleaned.substr(pos, nl - pos);
        ++line_no;

        std::smatch m;
        if (std::regex_match(line, m, class_re)) {
            Symbol s;
            s.kind = m[1].str();
            s.name = m[2].str();
            s.qualified_name = s.name;
            s.file_path = file_path;
            s.start_line = line_no;
            auto it = close_for_open_line.find(line_no);
            s.end_line = it != close_for_open_line.end() ? it->second : line_no;
            s.signature = detail::trim(line);
            symbols.push_back(std::move(s));
        } else if (std::regex_match(line, m, func_re)) {
            const std::string head = m[1].str();
            const std::string args = m[2].str();
            const std::string token = detail::trailing_identifier(head);
            if (!token.empty()) {
                std::string qualifier;
                std::string name = token;
                std::size_t sep = token.rfind("::");
                if (sep != std::string::npos) {
                    qualifier = token.substr(0, sep);
                    name = token.substr(sep + 2);
                }
                if (!name.empty() && !detail::cpp_keywords().count(name) &&
                    !detail::cpp_keywords().count(qualifier)) {
                    Symbol s;
                    s.kind = qualifier.empty() ? "function" : "method";
                    s.name = name;
                    s.qualified_name = qualifier.empty()
                                            ? name
                                            : qualifier + "::" + name;
                    s.file_path = file_path;
                    s.start_line = line_no;
                    auto it = close_for_open_line.find(line_no);
                    s.end_line =
                        it != close_for_open_line.end() ? it->second : line_no;
                    s.signature = detail::trim(head) + "(" + detail::trim(args) + ")";
                    symbols.push_back(std::move(s));
                }
            }
        }

        if (nl == std::string::npos) break;
        pos = nl + 1;
    }

    return symbols;
}

}  // namespace indexer
