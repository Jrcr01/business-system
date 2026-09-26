Attribute VB_Name = "InvoiceEntry"
Option Explicit

' Invoice entry helpers -- pairs with the frmInvoice pop-up form.
' One tab = one batch. The tab name starts with the supplier's name; a second batch for the same
' supplier becomes "Supplier (2)", "Supplier (3)" ... The AP system matches tabs to suppliers by name.

Public Sub Auto_Open()
    Dim ws As Worksheet, b As Object
    Application.OnKey "^+i", "ShowInvoiceForm"
    On Error Resume Next
    Set ws = ThisWorkbook.Worksheets("_Start")
    If Not ws Is Nothing Then
        ws.Buttons.Delete
        Set b = ws.Buttons.Add(ws.Range("B14").Left, ws.Range("B14").Top, 200, 34)
        b.Caption = "Add invoice  (Ctrl+Shift+I)"
        b.OnAction = "ShowInvoiceForm"
    End If
End Sub

Public Sub ShowInvoiceForm()
    frmInvoice.Show vbModeless
End Sub

Public Function BaseTabName(ByVal supplier As String) As String
    Dim s As String, ch As Variant
    s = supplier
    For Each ch In Array("\", "/", "?", "*", "[", "]", ":", "#")
        s = Replace(s, ch, " ")
    Next ch
    s = Application.WorksheetFunction.Trim(s)
    If Len(s) > 26 Then s = Trim(Left(s, 26))
    BaseTabName = s
End Function

Public Function IsBatchOf(ByVal sheetName As String, ByVal base As String) As Boolean
    IsBatchOf = (sheetName = base) Or (sheetName Like base & " (#)") Or (sheetName Like base & " (##)")
End Function

Public Function SheetExists(ByVal sheetName As String) As Boolean
    Dim ws As Worksheet
    For Each ws In ThisWorkbook.Worksheets
        If StrComp(ws.Name, sheetName, vbTextCompare) = 0 Then SheetExists = True: Exit Function
    Next ws
End Function

Public Function NewBatchSheet(ByVal base As String) As Worksheet
    Dim n As Long, nm As String, ws As Worksheet
    nm = base
    n = 1
    Do While SheetExists(nm)
        n = n + 1
        nm = base & " (" & n & ")"
    Loop
    Set ws = ThisWorkbook.Worksheets.Add(After:=ThisWorkbook.Worksheets(ThisWorkbook.Worksheets.Count))
    ws.Name = nm
    ws.Range("A1").Value = "Doc Ref"
    ws.Range("B1").Value = "Date"
    ws.Range("C1").Value = "Amount"
    ws.Range("D1").Value = "Less %"
    With ws.Range("A1:D1")
        .Font.Bold = True
        .Font.Color = RGB(255, 255, 255)
        .Interior.Color = RGB(31, 58, 95)
    End With
    ws.Columns("A").ColumnWidth = 22
    ws.Columns("B").ColumnWidth = 14
    ws.Columns("C").ColumnWidth = 16
    ws.Columns("D").ColumnWidth = 10
    ws.Columns("B").NumberFormat = "yyyy-mm-dd"
    ws.Columns("C").NumberFormat = "#,##0.00"
    ws.Range("F1").Value = "Invoices"
    ws.Range("G1").Formula = "=COUNTA(A2:A5000)"
    ws.Range("F2").Value = "Total"
    ws.Range("G2").Formula = "=SUM(C2:C5000)"
    ws.Range("G2").NumberFormat = "#,##0.00"
    ws.Range("F1:F2").Font.Bold = True
    Set NewBatchSheet = ws
End Function

' Name of the first tab of this supplier that already holds this Doc Ref, or "" if none.
Public Function FindDuplicate(ByVal base As String, ByVal docRef As String) As String
    Dim ws As Worksheet, r As Long, last As Long
    For Each ws In ThisWorkbook.Worksheets
        If IsBatchOf(ws.Name, base) Then
            last = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row
            For r = 2 To last
                If StrComp(Trim(CStr(ws.Cells(r, 1).Value)), docRef, vbTextCompare) = 0 Then
                    FindDuplicate = ws.Name
                    Exit Function
                End If
            Next r
        End If
    Next ws
End Function
