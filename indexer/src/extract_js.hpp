// Naive JavaScript/TypeScript symbol extraction (M7,
// docs/internal/NATIVE_INDEXER_PLAN.md). Same approach and the same
// accepted limitations as extract_cpp.hpp (regex/line-based, not an AST,
// single-line signatures only) — see that file's header comment for the
// full list; not repeated here.
//
// Detects:
//   - class declarations ending in `{` on the same line (`class Foo {`,
//     `class Foo extends Bar {`).
//   - function declarations (`function name(args) {`, `async function*
//     name(args) {`).
//   - arrow functions assigned to a variable (`const name = (args) =>
//     {`, `const name = arg => {`).
//   - bare `name(args) {` — object/class method shorthand. Tagged "method"
//     when it falls inside a previously-detected class's line range,
//     "function" otherwise (so a plain object literal's methods are
//     still reported, just not qualified).
// TS type annotations on parameters (`(a: number, b: string)`) don't
// break the arg capture since args are captured as opaque text, not
// parsed.

#pragma once

#include <regex>
#include <string>
#include <unordered_set>
#include <vector>

#include "lang_common.hpp"
#include "store.hpp"

namespace indexer {

namespace detail {

inline const std::unordered_set<std::string>& js_keywords() {
    static const std::unordered_set<std::string> kw = {
        "if",     "for",     "while",  "switch", "catch",  "return",
        "typeof", "new",     "delete", "do",     "else",   "try",
        "finally","class",   "const",  "let",    "var",    "in",
        "of",     "yield",   "await",  "super",  "this",   "function",
        "static", "async",   "get",    "set",    "export", "default",
    };
    return kw;
}

}  // namespace detail

inline std::vector<Symbol> extract_js_symbols(const std::string& file_path,
                                               const std::string& source) {
    std::vector<Symbol> symbols;

    const std::string cleaned = detail::strip_comments(source);
    const auto close_for_open_line = detail::match_braces(cleaned);

    static const std::regex class_re(
        R"(^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)\b[^{]*\{\s*$)");
    static const std::regex func_decl_re(
        R"(^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+([A-Za-z_$][\w$]*)\s*\(([^()]*)\)\s*\{\s*$)");
    static const std::regex arrow_re(
        R"(^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(?([^()=]*)\)?\s*=>\s*\{\s*$)");
    static const std::regex method_re(
        R"(^\s*(.+?)\(([^()]*)\)\s*\{\s*$)");

    struct ClassScope {
        std::string name;
        int end_line;
    };

    std::vector<std::string> lines;
    {
        std::size_t pos = 0;
        while (pos <= cleaned.size()) {
            std::size_t nl = cleaned.find('\n', pos);
            lines.push_back(nl == std::string::npos ? cleaned.substr(pos)
                                                      : cleaned.substr(pos, nl - pos));
            if (nl == std::string::npos) break;
            pos = nl + 1;
        }
    }

    std::vector<ClassScope> active_class_stack;

    for (std::size_t idx = 0; idx < lines.size(); ++idx) {
        const int line_no = static_cast<int>(idx) + 1;
        const std::string& line = lines[idx];

        while (!active_class_stack.empty() &&
               active_class_stack.back().end_line < line_no) {
            active_class_stack.pop_back();
        }

        auto end_line_for = [&](int start) {
            auto it = close_for_open_line.find(start);
            return it != close_for_open_line.end() ? it->second : start;
        };

        std::smatch m;
        if (std::regex_match(line, m, class_re)) {
            Symbol s;
            s.kind = "class";
            s.name = m[1].str();
            s.qualified_name = s.name;
            s.file_path = file_path;
            s.start_line = line_no;
            s.end_line = end_line_for(line_no);
            s.signature = detail::trim(line);
            symbols.push_back(s);
            active_class_stack.push_back({s.name, s.end_line});
            continue;
        }

        if (std::regex_match(line, m, func_decl_re) ||
            std::regex_match(line, m, arrow_re)) {
            const std::string name = m[1].str();
            if (!detail::js_keywords().count(name)) {
                Symbol s;
                s.kind = "function";
                s.name = name;
                s.qualified_name = name;
                s.file_path = file_path;
                s.start_line = line_no;
                s.end_line = end_line_for(line_no);
                s.signature = detail::trim(line);
                symbols.push_back(std::move(s));
            }
            continue;
        }

        if (std::regex_match(line, m, method_re)) {
            const std::string head = m[1].str();
            const std::string args = m[2].str();
            const std::string name = detail::trailing_identifier(head);
            if (!name.empty() && !detail::js_keywords().count(name)) {
                const bool in_class = !active_class_stack.empty();
                Symbol s;
                s.kind = in_class ? "method" : "function";
                s.name = name;
                s.qualified_name =
                    in_class ? active_class_stack.back().name + "." + name : name;
                s.file_path = file_path;
                s.start_line = line_no;
                s.end_line = end_line_for(line_no);
                s.signature = detail::trim(head) + "(" + detail::trim(args) + ")";
                symbols.push_back(std::move(s));
            }
        }
    }

    return symbols;
}

}  // namespace indexer
