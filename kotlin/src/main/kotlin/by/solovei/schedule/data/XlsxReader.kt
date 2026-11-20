package by.solovei.schedule.data

import org.xml.sax.Attributes
import org.xml.sax.helpers.DefaultHandler
import java.io.ByteArrayInputStream
import java.io.InputStream
import java.util.zip.ZipInputStream
import javax.xml.parsers.SAXParserFactory

/** Лист таблицы: значения ячеек и объединения. Координаты с нуля. */
class Sheet(
    val name: String,
    private val cells: Map<Long, String>,
    val merges: List<Merge>,
    val rowCount: Int,
) {
    data class Merge(val r1: Int, val c1: Int, val r2: Int, val c2: Int)

    private val mergeByAnchor = merges.associateBy { key(it.r1, it.c1) }

    fun text(row: Int, col: Int): String = cells[key(row, col)]?.trim().orEmpty()

    /** Объединение, левым верхним углом которого является ячейка (или сама ячейка 1x1). */
    fun span(row: Int, col: Int): Merge =
        mergeByAnchor[key(row, col)] ?: Merge(row, col, row, col)

    /** Ячейка накрыта объединением, но не является его началом. */
    fun isCovered(row: Int, col: Int): Boolean = merges.any {
        row in it.r1..it.r2 && col in it.c1..it.c2 && !(row == it.r1 && col == it.c1)
    }

    companion object {
        fun key(row: Int, col: Int): Long = row.toLong() * 100_000 + col
    }
}

/** Минимальный читатель .xlsx без сторонних библиотек: zip + SAX. */
object XlsxReader {

    fun read(input: InputStream): List<Sheet> {
        val files = HashMap<String, ByteArray>()
        ZipInputStream(input).use { zip ->
            while (true) {
                val e = zip.nextEntry ?: break
                val n = e.name
                if (n == "xl/workbook.xml" || n == "xl/sharedStrings.xml" ||
                    n == "xl/_rels/workbook.xml.rels" || n.startsWith("xl/worksheets/sheet")
                ) files[n] = zip.readBytes()
            }
        }
        val workbook = files["xl/workbook.xml"] ?: error("Это не xlsx-файл")
        val strings = files["xl/sharedStrings.xml"]?.let(::readSharedStrings) ?: emptyList()
        val rels = files["xl/_rels/workbook.xml.rels"]?.let(::readRels) ?: emptyMap()

        val sheets = ArrayList<Sheet>()
        parse(workbook, object : DefaultHandler() {
            override fun startElement(uri: String?, local: String?, q: String, a: Attributes) {
                if (q != "sheet") return
                val name = a.getValue("name") ?: return
                val target = rels[a.getValue("r:id")] ?: return
                val bytes = files["xl/" + target.removePrefix("/xl/")] ?: return
                sheets += readSheet(name, bytes, strings)
            }
        })
        return sheets
    }

    private fun parse(bytes: ByteArray, handler: DefaultHandler) {
        val factory = SAXParserFactory.newInstance()
        factory.isNamespaceAware = false
        factory.newSAXParser().parse(ByteArrayInputStream(bytes), handler)
    }

    private fun readRels(bytes: ByteArray): Map<String, String> {
        val out = HashMap<String, String>()
        parse(bytes, object : DefaultHandler() {
            override fun startElement(uri: String?, local: String?, q: String, a: Attributes) {
                if (q == "Relationship") out[a.getValue("Id")] = a.getValue("Target")
            }
        })
        return out
    }

    private fun readSharedStrings(bytes: ByteArray): List<String> {
        val out = ArrayList<String>()
        parse(bytes, object : DefaultHandler() {
            val sb = StringBuilder()
            var inT = false
            var inPh = false
            override fun startElement(uri: String?, local: String?, q: String, a: Attributes) {
                when (q) {
                    "si" -> sb.setLength(0)
                    "t" -> inT = !inPh
                    "rPh" -> inPh = true
                }
            }
            override fun endElement(uri: String?, local: String?, q: String) {
                when (q) {
                    "si" -> out += sb.toString()
                    "t" -> inT = false
                    "rPh" -> inPh = false
                }
            }
            override fun characters(ch: CharArray, start: Int, length: Int) {
                if (inT) sb.append(ch, start, length)
            }
        })
        return out
    }

    private fun readSheet(name: String, bytes: ByteArray, strings: List<String>): Sheet {
        val cells = HashMap<Long, String>()
        val merges = ArrayList<Sheet.Merge>()
        var maxRow = 0
        parse(bytes, object : DefaultHandler() {
            val sb = StringBuilder()
            var ref = ""
            var type = ""
            var capture = false
            override fun startElement(uri: String?, local: String?, q: String, a: Attributes) {
                when (q) {
                    "c" -> { ref = a.getValue("r") ?: ""; type = a.getValue("t") ?: ""; sb.setLength(0) }
                    "v", "t" -> capture = true
                    "mergeCell" -> a.getValue("ref")?.let { r ->
                        val parts = r.split(":")
                        if (parts.size == 2) {
                            val (r1, c1) = cellRef(parts[0]); val (r2, c2) = cellRef(parts[1])
                            merges += Sheet.Merge(r1, c1, r2, c2)
                        }
                    }
                }
            }
            override fun endElement(uri: String?, local: String?, q: String) {
                when (q) {
                    "v", "t" -> capture = false
                    "c" -> {
                        if (sb.isEmpty() || ref.isEmpty()) return
                        val raw = sb.toString()
                        val value = if (type == "s") strings.getOrNull(raw.toIntOrNull() ?: -1) ?: "" else raw
                        if (value.isNotBlank()) {
                            val (r, c) = cellRef(ref)
                            cells[Sheet.key(r, c)] = value
                            if (r > maxRow) maxRow = r
                        }
                    }
                }
            }
            override fun characters(ch: CharArray, start: Int, length: Int) {
                if (capture) sb.append(ch, start, length)
            }
        })
        return Sheet(name, cells, merges, maxRow + 1)
    }

    /** "AB12" -> (row=11, col=27) */
    private fun cellRef(ref: String): Pair<Int, Int> {
        var col = 0
        var i = 0
        while (i < ref.length && ref[i].isLetter()) { col = col * 26 + (ref[i].uppercaseChar() - 'A' + 1); i++ }
        val row = ref.substring(i).toIntOrNull() ?: 1
        return (row - 1) to (col - 1)
    }
}
