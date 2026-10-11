import type { ReactNode } from "react";

function inline(text: string): ReactNode[] {
  return text
    .split(/(\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*)/g)
    .map((part, index) => {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part);
      if (link?.[1] && link[2] && /^[a-z0-9-]+\.md$/i.test(link[2]))
        return (
          <a key={index} href={`#/legal/${link[2].replace(/\.md$/i, "")}`}>
            {link[1]}
          </a>
        );
      if (link?.[1] && link[2]) {
        try {
          const destination = new URL(link[2]);
          if (destination.protocol === "https:")
            return (
              <a
                key={index}
                href={destination.href}
                target="_blank"
                rel="noopener noreferrer"
              >
                {link[1]}
              </a>
            );
        } catch {
          // Only canonical document basenames and valid HTTPS links are navigable.
        }
        return link[1];
      }
      if (part.startsWith("**"))
        return <strong key={index}>{part.slice(2, -2)}</strong>;
      return part;
    });
}

/** Render canonical legal Markdown as safe text, headings, tables and allowlisted links. */
export function LegalText({ text }: { text: string }) {
  return <div className="legal-text">{text.split(/\n\s*\n/).map((block, index) => {
          if (block.startsWith("# "))
            return <h1 key={index}>{inline(block.slice(2))}</h1>;
          if (block.startsWith("## "))
            return <h2 key={index}>{inline(block.slice(3))}</h2>;
          if (block.startsWith("|")) {
            const rows = block
              .split("\n")
              .filter((line) => !/^\|[\s:|-]+\|$/.test(line));
            return (
              <div className="legal-table" key={index}>
                <table>
                  <tbody>
                    {rows.map((row, rowIndex) => (
                      <tr key={rowIndex}>
                        {row
                          .split("|")
                          .slice(1, -1)
                          .map((cell, cellIndex) =>
                            rowIndex === 0 ? (
                              <th key={cellIndex}>{inline(cell.trim())}</th>
                            ) : (
                              <td key={cellIndex}>{inline(cell.trim())}</td>
                            ),
                          )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          }
          return <p key={index}>{inline(block)}</p>;
        })}</div>;
}
