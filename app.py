from flask import Flask, render_template, redirect, url_for, request, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime
import os
import csv
import openpyxl
import io
import json
import unicodedata
import pandas as pd

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta_aqui'

# --- FUNÇÕES AUXILIARES DE TEXTO ---
def normalizar_texto(texto):
    """Remove acentos, espaços extras e converte para minúsculo"""
    if not texto:
        return ""
    texto_str = str(texto).strip().lower()
    return unicodedata.normalize('NFKD', texto_str).encode('ASCII', 'ignore').decode('utf-8')

# --- FUNÇÕES DE CARREGAMENTO E SALVAMENTO DE DADOS ---
def carregar_dados(caminho_arquivo, default_data):
    os.makedirs(os.path.dirname(caminho_arquivo), exist_ok=True)
    if not os.path.exists(caminho_arquivo) or os.path.getsize(caminho_arquivo) == 0:
        with open(caminho_arquivo, 'w', encoding='utf-8') as f:
            json.dump(default_data, f)
    with open(caminho_arquivo, 'r', encoding='utf-8') as f:
        return json.load(f)

def salvar_dados(caminho_arquivo, data):
    with open(caminho_arquivo, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

def carregar_usuarios():
    caminho = os.path.join('data', 'usuarios.json')
    if not os.path.exists(caminho):
        return []
    with open(caminho, 'r', encoding='utf-8') as f:
        return json.load(f)

def salvar_usuarios(usuarios):
    salvar_dados('data/usuarios.json', usuarios)

def carregar_materiais():
    return carregar_dados('data/materiais.json', [])

def salvar_materiais(materiais):
    salvar_dados('data/materiais.json', materiais)

def carregar_requisicoes():
    return carregar_dados('data/requisicoes.json', [])

def salvar_requisicoes(requisicoes):
    salvar_dados('data/requisicoes.json', requisicoes)

def carregar_departamentos():
    return carregar_dados('data/departamentos.json', ["Logística", "Recursos Humanos", "Financeiro", "TI", "Produção", "MAF Betim", "MAF Porto Real"])

def salvar_departamentos(departamentos):
    salvar_dados('data/departamentos.json', departamentos)

# --- DECORADORES DE AUTENTICAÇÃO E PERMISSÃO ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash('Por favor, faça login para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        
        if session.get('primeiro_acesso') and request.endpoint not in ['trocar_senha_obrigatoria', 'logout', 'static']:
            flash('Sua senha precisa ser alterada no primeiro acesso/reset antes de continuar.', 'info')
            return redirect(url_for('trocar_senha_obrigatoria'))
            
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        permissao = session.get('user', {}).get('permissao')
        if permissao not in ['admin', 'administrador']:
            flash('Acesso restrito a Administradores.', 'danger')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

def gestor_required(f):
    """Permite acesso para Admin e Abastecedor"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        permissao = session.get('user', {}).get('permissao')
        if permissao not in ['admin', 'administrador', 'abastecedor']:
            flash('Acesso restrito a Administradores e Abastecedores.', 'danger')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

def solicitante_ou_admin_required(f):
    """Permite criar requisições apenas para Solicitante e Administrador"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        permissao = session.get('user', {}).get('permissao')
        if permissao not in ['solicitante', 'admin', 'administrador']:
            flash('Usuários do perfil Abastecedor não possuem permissão para realizar requisições.', 'warning')
            return redirect(url_for('pendentes'))
        return f(*args, **kwargs)
    return decorated_function

# --- ROTAS PRINCIPAIS ---
@app.route('/')
def home():
    if 'user' in session:
        permissao = session['user'].get('permissao')
        if permissao in ['admin', 'administrador', 'abastecedor']:
            return redirect(url_for('pendentes'))
        else:
            return redirect(url_for('fazer_requisicao'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        senha = request.form.get('senha', '').strip()

        usuarios = carregar_usuarios()
        
        # Busca o usuário pelo Nome de Usuário (username)
        usuario_encontrado = next(
            (u for u in usuarios if str(u.get('username', '')).strip().lower() == username or str(u.get('registro', '')).strip().lower() == username), 
            None
        )

        if usuario_encontrado:
            senha_salva = usuario_encontrado.get('senha', '')
            senha_valida = False
            
            if senha_salva.startswith('scrypt:') or senha_salva.startswith('pbkdf2:'):
                senha_valida = check_password_hash(senha_salva, senha)
            else:
                senha_valida = (senha_salva == senha)

            if senha_valida:
                session['user'] = {
                    'username': usuario_encontrado.get('username'),
                    'registro': usuario_encontrado.get('registro'),
                    'nome': usuario_encontrado.get('nome'),
                    'permissao': usuario_encontrado.get('permissao') or usuario_encontrado.get('perfil'),
                    'departamento': usuario_encontrado.get('departamento', 'Geral')
                }
                
                session['primeiro_acesso'] = usuario_encontrado.get('primeiro_acesso', False)

                flash('Login realizado com sucesso!', 'success')

                if session.get('primeiro_acesso'):
                    return redirect(url_for('trocar_senha_obrigatoria'))

                return redirect(url_for('home'))

        flash('Usuário ou senha incorretos. Tente novamente.', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/trocar_senha_obrigatoria', methods=['GET', 'POST'])
def trocar_senha_obrigatoria():
    if 'user' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        nova_senha = request.form.get('nova_senha')
        confirmacao = request.form.get('confirmacao_senha')

        if nova_senha != confirmacao:
            flash('As senhas não coincidem. Tente novamente.', 'danger')
            return render_template('trocar_senha_obrigatoria.html')

        usuarios = carregar_usuarios()
        registro_logado = str(session['user']['registro']).strip()

        for u in usuarios:
            reg_u = str(u.get('registro') or u.get('username', '')).strip()
            if reg_u == registro_logado:
                u['senha'] = generate_password_hash(nova_senha)
                u['primeiro_acesso'] = False
                break
        
        salvar_usuarios(usuarios)
        session['primeiro_acesso'] = False
        flash('Senha alterada com sucesso! Bem-vindo ao sistema.', 'success')
        return redirect(url_for('home'))

    return render_template('trocar_senha_obrigatoria.html')

@app.route('/trocar_senha', methods=['GET', 'POST'])
@login_required
def trocar_senha():
    if request.method == 'POST':
        senha_atual = request.form.get('senha_atual', '').strip()
        nova_senha = request.form.get('nova_senha', '').strip()
        confirmacao = request.form.get('confirmacao_senha', '').strip()

        if nova_senha != confirmacao:
            flash('A nova senha e a confirmação não coincidem.', 'danger')
            return render_template('trocar_senha.html')

        usuarios = carregar_usuarios()
        registro_logado = str(session['user']['registro']).strip()

        for u in usuarios:
            reg_u = str(u.get('registro') or u.get('username', '')).strip()
            if reg_u == registro_logado:
                senha_salva = u.get('senha', '')
                senha_valida = False
                
                if senha_salva.startswith('scrypt:') or senha_salva.startswith('pbkdf2:'):
                    senha_valida = check_password_hash(senha_salva, senha_atual)
                else:
                    senha_valida = (senha_salva == senha_atual)

                if not senha_valida:
                    flash('Senha atual incorreta.', 'danger')
                    return render_template('trocar_senha.html')

                u['senha'] = generate_password_hash(nova_senha)
                u['primeiro_acesso'] = False
                salvar_usuarios(usuarios)
                flash('Sua senha foi alterada com sucesso!', 'success')
                return redirect(url_for('home'))

        flash('Usuário não encontrado.', 'danger')

    return render_template('trocar_senha.html')

@app.route('/fazer_requisicao')
@login_required
@solicitante_ou_admin_required
def fazer_requisicao():
    materiais = carregar_materiais()
    departamentos = carregar_departamentos()
    
    usuario_logado = session['user']
    registro_logado = str(usuario_logado.get('registro')).strip()
    permissao_usuario = usuario_logado.get('permissao', '')
    
    # Busca as informações atualizadas do usuário no banco/json
    usuarios = carregar_usuarios()
    dados_usuario = next((u for u in usuarios if str(u.get('registro') or u.get('username', '')).strip() == registro_logado), {})
    
    # Acesso permitido se for Admin OU se a flag individual pode_solicitar_epi for True
    pode_acessar_epi = (
        permissao_usuario in ['admin', 'administrador'] or 
        dados_usuario.get('pode_solicitar_epi', False) is True
    )
    
    categoria_atual = request.args.get('categoria', 'escritorio')
    
    if categoria_atual == 'epi' and not pode_acessar_epi:
        flash('Seu usuário não possui permissão para solicitar EPIs. Contate o administrador.', 'warning')
        return redirect(url_for('fazer_requisicao', categoria='escritorio'))
    
    materiais_filtrados = [
        m for m in materiais 
        if m.get('categoria', 'escritorio') == categoria_atual
    ]
    
    return render_template(
        'fazer_requisicao.html', 
        materiais=materiais_filtrados, 
        departamentos=departamentos,
        categoria_atual=categoria_atual,
        pode_acessar_epi=pode_acessar_epi
    )

@app.route('/enviar_requisicao', methods=['POST'])
@login_required
@solicitante_ou_admin_required
def enviar_requisicao():
    materiais_disponiveis = carregar_materiais()
    itens_requisicao = []

    categoria_solicitacao = request.form.get('categoria_solicitacao', 'escritorio')

    for key, value in request.form.items():
        if key.startswith('materiais[') and key.endswith('][codigo]'):
            index = key.split('[')[1].split(']')[0]
            codigo_material = value
            quantidade = request.form.get(f'materiais[{index}][quantidade]')

            if not codigo_material or not quantidade:
                continue

            quantidade = int(quantidade)

            # Busca o material considerando o CÓDIGO e a CATEGORIA selecionada
            material_encontrado = next(
                (
                    m for m in materiais_disponiveis 
                    if str(m['codigo']) == str(codigo_material) 
                    and m.get('categoria', categoria_solicitacao) == categoria_solicitacao
                ), 
                None
            )
            
            if not material_encontrado:
                flash(f'Material com código {codigo_material} não encontrado na categoria selecionada.', 'danger')
                return redirect(url_for('fazer_requisicao'))

            if quantidade > material_encontrado['quantidade_maxima']:
                flash(f"A quantidade de '{material_encontrado['descricao']}' excede o limite de {material_encontrado['quantidade_maxima']} por requisição.", 'warning')
                return redirect(url_for('fazer_requisicao'))
            
            itens_requisicao.append({
                'codigo': codigo_material,
                'nome': material_encontrado['descricao'],
                'quantidade': quantidade,
                'quantidade_solicitada': quantidade,
                'quantidade_separada': quantidade
            })

    if not itens_requisicao:
        flash('Nenhum item foi adicionado à requisição.', 'warning')
        return redirect(url_for('fazer_requisicao'))

    requisicoes = carregar_requisicoes()
    usuario_logado = session['user']
    
    nova_requisicao = {
        'id': len(requisicoes) + 1,
        'nome': usuario_logado.get('nome'),
        'username': usuario_logado.get('username'),
        'registro': usuario_logado.get('registro'),
        'departamento': usuario_logado.get('departamento'),
        'categoria': categoria_solicitacao,
        'data': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'status': 'pendente',
        'itens': itens_requisicao
    }

    requisicoes.append(nova_requisicao)
    salvar_requisicoes(requisicoes)
    
    flash('Sua requisição foi enviada com sucesso e está pendente de separação!', 'success')
    return redirect(url_for('fazer_requisicao', categoria=categoria_solicitacao))

@app.route('/pendentes')
@login_required
@gestor_required
def pendentes():
    requisicoes = carregar_requisicoes()
    pendentes_list = [r for r in requisicoes if r.get('status') == 'pendente']
    return render_template('pendentes.html', requisicoes=pendentes_list)

@app.route('/concluir_requisicao', methods=['POST'])
@login_required
@gestor_required
def concluir_requisicao():
    requisicao_id = int(request.form.get('requisicao_id'))
    requisicoes = carregar_requisicoes()
    materiais = carregar_materiais()
    requisicao = next((r for r in requisicoes if r['id'] == requisicao_id), None)
    
    if requisicao:
        for item in requisicao.get('itens', []):
            codigo_item = str(item.get('codigo', ''))
            nova_qtd_str = request.form.get(f'qtd_item_{codigo_item}')
            
            if 'quantidade_solicitada' not in item:
                item['quantidade_solicitada'] = item.get('quantidade', 0)
            
            if nova_qtd_str is not None:
                try:
                    nova_qtd = int(nova_qtd_str)
                    
                    # Localiza o cadastro original do material para obter o limite do sistema
                    mat_cadastrado = next((m for m in materiais if str(m['codigo']) == codigo_item), None)
                    limite_max = mat_cadastrado['quantidade_maxima'] if mat_cadastrado else 9999
                    
                    if nova_qtd > limite_max:
                        flash(f"A quantidade do item '{item.get('nome')}' não pode ultrapassar o limite cadastrado no sistema ({limite_max}).", 'warning')
                        return redirect(url_for('ver_requisicao', requisicao_id=requisicao_id))
                    
                    item['quantidade_separada'] = max(0, nova_qtd)
                except ValueError:
                    pass

        requisicao['status'] = 'separado'
        requisicao['data_retirada'] = datetime.now().strftime('%d/%m/%Y %H:%M')
        requisicao['separado_por'] = session['user'].get('nome', '')
        
        salvar_requisicoes(requisicoes)
        flash(f'Requisição #{requisicao_id} separada com sucesso!', 'success')
        return redirect(url_for('pendentes'))
    else:
        flash('Requisição não encontrada.', 'danger')
        return redirect(url_for('pendentes'))

@app.route('/separados')
@login_required
@gestor_required
def separados():
    requisicoes = carregar_requisicoes()
    separados_list = [r for r in requisicoes if r.get('status') == 'separado']
    return render_template('separados.html', requisicoes=separados_list)

@app.route('/finalizar_requisicao', methods=['POST'])
@login_required
@gestor_required
def finalizar_requisicao():
    requisicao_id = str(request.form.get('requisicao_id'))
    requisicoes = carregar_requisicoes()
    requisicao = next((r for r in requisicoes if str(r['id']) == requisicao_id), None)

    if not requisicao:
        flash('Requisição não encontrada.', 'danger')
        return redirect(url_for('separados'))

    categoria = requisicao.get('categoria', 'escritorio')
    usuario_logado = session['user']
    
    # --- FLUXO PARA EPIs / UNIDADES REMOTAS (Validação via NF + Senha do Abastecedor) ---
    if categoria == 'epi':
        numero_nf = request.form.get('numero_nf', '').strip()
        senha_abastecedor = request.form.get('senha_abastecedor', '').strip()

        if not numero_nf:
            flash('Por favor, informe o número da Nota Fiscal de Transferência.', 'warning')
            return redirect(url_for('separados'))

        # Valida a senha do próprio abastecedor logado
        registro_logado = str(usuario_logado.get('registro')).strip()
        usuarios = carregar_usuarios()
        abastecedor_atual = next((u for u in usuarios if str(u.get('registro') or u.get('username', '')).strip() == registro_logado), None)

        if not abastecedor_atual:
            flash('Sessão inválida. Faça login novamente.', 'danger')
            return redirect(url_for('separados'))

        senha_salva = abastecedor_atual.get('senha', '')
        senha_valida = check_password_hash(senha_salva, senha_abastecedor) if (senha_salva.startswith('scrypt:') or senha_salva.startswith('pbkdf2:')) else (senha_salva == senha_abastecedor)

        if not senha_valida:
            flash('Sua senha de confirmação está incorreta. A baixa não foi realizada.', 'danger')
            return redirect(url_for('separados'))

        # Grava os dados de envio da NF
        requisicao['status'] = 'concluida'
        requisicao['nota_fiscal'] = numero_nf
        requisicao['data_conclusao'] = datetime.now().strftime('%d/%m/%Y %H:%M')
        requisicao['finalizado_por'] = usuario_logado.get('nome', '')
        salvar_requisicoes(requisicoes)

        flash(f'Requisição #{requisicao_id} enviada com sucesso! NF: {numero_nf}', 'success')
        return redirect(url_for('separados'))

    # --- FLUXO PARA MATERIAIS DE ESCRITÓRIO (Validação por Senha do Solicitante) ---
    registro_solicitante = str(request.form.get('registro_solicitante', '')).strip()
    senha_solicitante = request.form.get('senha_solicitante')

    registro_original = str(requisicao.get('registro', '')).strip()
    if registro_solicitante != registro_original:
        flash(f'Atenção: Apenas o próprio solicitante (Registro {registro_original}) pode confirmar a retirada deste pedido.', 'danger')
        return redirect(url_for('separados'))

    usuarios = carregar_usuarios()
    solicitante = next((u for u in usuarios if str(u.get('registro') or u.get('username', '')).strip() == registro_solicitante), None)

    if not solicitante:
        flash('Registro do solicitante não encontrado.', 'danger')
        return redirect(url_for('separados'))

    senha_salva = solicitante.get('senha', '')
    senha_valida = check_password_hash(senha_salva, senha_solicitante) if (senha_salva.startswith('scrypt:') or senha_salva.startswith('pbkdf2:')) else (senha_salva == senha_solicitante)

    if not senha_valida:
        flash('Senha incorreta. A requisição não foi finalizada.', 'danger')
        return redirect(url_for('separados'))

    requisicao['status'] = 'concluida'
    requisicao['data_conclusao'] = datetime.now().strftime('%d/%m/%Y %H:%M')
    requisicao['finalizado_por'] = usuario_logado.get('nome', '')
    salvar_requisicoes(requisicoes)

    flash('Requisição finalizada com sucesso!', 'success')
    return redirect(url_for('separados'))

# --- HISTÓRICO E EXPORTAÇÃO ---
@app.route('/historico')
@login_required
def historico():
    requisicoes = carregar_requisicoes()
    permissao = session['user'].get('permissao')
    
    if permissao in ['admin', 'administrador', 'abastecedor']:
        requisicoes_a_exibir = requisicoes
    else:
        registro_usuario = str(session['user'].get('registro', '')).strip()
        requisicoes_a_exibir = [
            req for req in requisicoes
            if str(req.get('registro', '')).strip() == registro_usuario
        ]

    requisicoes_ordenadas = requisicoes_a_exibir[::-1]
    return render_template('historico.html', requisicoes=requisicoes_ordenadas)

@app.route('/excluir_requisicao/<int:requisicao_id>', methods=['POST'])
@login_required
def excluir_requisicao(requisicao_id):
    requisicoes = carregar_requisicoes()
    requisicao_para_excluir = next((req for req in requisicoes if req['id'] == requisicao_id), None)

    if not requisicao_para_excluir:
        flash('Requisição não encontrada.', 'danger')
        return redirect(url_for('historico'))

    usuario_logado = session['user']
    registro_logado = str(usuario_logado.get('registro', '')).strip()
    permissao_logada = usuario_logado.get('permissao', '')

    if permissao_logada in ['admin', 'administrador']:
        requisicoes.remove(requisicao_para_excluir)
        salvar_requisicoes(requisicoes)
        flash(f'Requisição #{requisicao_id} excluída com sucesso.', 'success')

    elif permissao_logada == 'abastecedor':
        flash('Usuários do perfil Abastecedor não têm permissão para excluir requisições.', 'danger')

    else:
        registro_pedido = str(requisicao_para_excluir.get('registro', '')).strip()
        status_pedido = requisicao_para_excluir.get('status')

        if registro_pedido == registro_logado:
            if status_pedido == 'pendente':
                requisicoes.remove(requisicao_para_excluir)
                salvar_requisicoes(requisicoes)
                flash(f'Sua requisição #{requisicao_id} foi excluída com sucesso!', 'success')
            else:
                flash('Não é possível excluir uma requisição que já foi separada ou concluída.', 'warning')
        else:
            flash('Você não tem permissão para excluir a requisição de outro usuário.', 'danger')

    return redirect(request.referrer or url_for('historico'))

@app.route('/exportar_relatorio_excel')
@login_required
@gestor_required
def exportar_relatorio_excel():
    requisicoes = carregar_requisicoes()
    
    # Obtém as datas informadas no filtro
    data_inicio_str = request.args.get('data_inicio')
    data_fim_str = request.args.get('data_fim')
    
    data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d') if data_inicio_str else None
    data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d') if data_fim_str else None

    linhas_relatorio = []

    for req in requisicoes:
        # Tenta converter a data da requisição para comparação (Formato esperado: DD/MM/YYYY ou YYYY-MM-DD)
        data_req_raw = req.get('data', '')
        try:
            if '/' in data_req_raw:
                data_req_dt = datetime.strptime(data_req_raw.split(' ')[0], '%d/%m/%Y')
            else:
                data_req_dt = datetime.strptime(data_req_raw.split(' ')[0], '%Y-%m-%d')
        except Exception:
            data_req_dt = None

        # Aplicação do Filtro por Data
        if data_inicio and data_req_dt and data_req_dt < data_inicio:
            continue
        if data_fim and data_req_dt and data_req_dt > data_fim:
            continue

        # Garante a leitura dos itens da requisição
        itens = req.get('itens', [])
        
        # Mapeamento do Status para o Excel
        status_traduzido = req.get('status', '').capitalize()
        if status_traduzido.lower() == 'concluida':
            status_traduzido = 'Concluída'

        categoria_traduzida = 'EPI' if req.get('categoria') == 'epi' else 'Escritório'

        # Se houver itens na requisição, gera uma linha por item
        if itens:
            for item in itens:
                linhas_relatorio.append({
                    'ID Requisição': req.get('id'),
    'Data Solicitação': req.get('data'),
    'Status': status_traduzido,
    'Tipo/Categoria': categoria_traduzida,
    'Nota Fiscal': req.get('nota_fiscal', '-'),
    'Solicitante': req.get('nome'),
    'Registro': req.get('registro'),
    'Departamento': req.get('departamento'),
    'Código Material': item.get('codigo', '-'),
    'Descrição Material': item.get('nome') or item.get('descricao', '-'),
    'Qtd Solicitada': item.get('quantidade_solicitada', item.get('quantidade', 0)),
    'Qtd Separada': item.get('quantidade_separada', item.get('quantidade', 0)),
    'Separado Por': req.get('separado_por', '-'),
    'Finalizado Por': req.get('finalizado_por', '-'),
    'Data Conclusão/Retirada': req.get('data_conclusao') or req.get('data_retirada') or '-'
})
        else:
            # Caso a requisição não possua itens especificados
            linhas_relatorio.append({
                'ID Requisição': req.get('id'),
                'Data Solicitação': req.get('data'),
                'Status': status_traduzido,
                'Tipo/Categoria': categoria_traduzida,
                'Nota Fiscal': req.get('nota_fiscal', '-'),
                'Solicitante': req.get('nome'),
                'Registro': req.get('registro'),
                'Departamento': req.get('departamento'),
                'Código Material': '-',
                'Descrição Material': 'Sem itens registrados',
                'Qtd Solicitada': 0,
                'Qtd Separada': 0,
                'Separado Por': req.get('separado_por', '-'),
                'Finalizado Por': req.get('finalizado_por', '-'),
                'Data Conclusão/Retirada': req.get('data_conclusao') or req.get('data_retirada') or '-'
            })

    if not linhas_relatorio:
        flash('Nenhuma requisição encontrada para o período selecionado.', 'warning')
        return redirect(url_for('historico'))

    # Gera a planilha usando a biblioteca Pandas
    df = pd.DataFrame(linhas_relatorio)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Histórico CoreStock')
    
    output.seek(0)
    nome_arquivo = f"Relatorio_CoreStock_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=nome_arquivo
    )

@app.route('/gerenciar_usuarios', methods=['GET', 'POST'])
@login_required
@admin_required
def gerenciar_usuarios():
    usuarios = carregar_usuarios()
    deps_raw = carregar_departamentos()
    
    departamentos = []
    for d in deps_raw:
        if isinstance(d, dict):
            departamentos.append(d)
        else:
            departamentos.append({'nome': str(d)})
    
    if request.method == 'POST':
        # Edição rápida de permissão de EPI
        if 'acao_editar_epi' in request.form:
            reg_target = str(request.form.get('registro_target')).strip()
            status_epi = request.form.get('pode_solicitar_epi') == 'true'
            
            for u in usuarios:
                reg_u = str(u.get('registro') or u.get('username', '')).strip()
                if reg_u == reg_target:
                    u['pode_solicitar_epi'] = status_epi
                    break
                    
            salvar_usuarios(usuarios)
            flash('Permissão de EPI atualizada com sucesso!', 'success')
            return redirect(url_for('gerenciar_usuarios'))

        # Cadastro de Novo Usuário (Nome de Usuário + Registro)
        username = str(request.form.get('username', '')).strip().lower()
        registro = str(request.form.get('registro', '')).strip()
        nome = request.form.get('nome', '').strip()
        centro_custo = request.form.get('centro_custo', '').strip()
        departamento = request.form.get('departamento')
        permissao = request.form.get('permissao')
        pode_solicitar_epi = 'pode_solicitar_epi' in request.form
        senha_inicial = "Mudar123@"

        # Verifica se já existe um usuário com este username ou registro
        if any(str(u.get('username', '')).strip().lower() == username for u in usuarios):
            flash('Nome de usuário já cadastrado! Escolha outro.', 'danger')
        elif any(str(u.get('registro', '')).strip() == registro for u in usuarios):
            flash('Registro (ID) já cadastrado!', 'danger')
        else:
            usuarios.append({
                "username": username,
                "registro": registro,
                "nome": nome,
                "centro_custo": centro_custo,
                "departamento": departamento,
                "permissao": permissao,
                "perfil": permissao,
                "pode_solicitar_epi": pode_solicitar_epi,
                "senha": senha_inicial,
                "primeiro_acesso": True
            })
            salvar_usuarios(usuarios)
            flash(f'Usuário {nome} (@{username}) criado com sucesso!', 'success')
            return redirect(url_for('gerenciar_usuarios'))

    return render_template('gerenciar_usuarios.html', usuarios=usuarios, departamentos=departamentos)

@app.route('/resetar_senha/<registro>', methods=['POST'])
@login_required
@admin_required
def resetar_senha(registro):
    usuarios = carregar_usuarios()
    senha_padrao = "Mudar123@"
    
    for u in usuarios:
        reg_u = str(u.get('registro') or u.get('username', '')).strip()
        if reg_u == str(registro).strip():
            u['senha'] = senha_padrao
            u['primeiro_acesso'] = True
            break
            
    salvar_usuarios(usuarios)
    flash(f'A senha do usuário {registro} foi resetada para "{senha_padrao}".', 'success')
    return redirect(url_for('gerenciar_usuarios'))

@app.route('/excluir_usuario', methods=['POST'])
@login_required
@admin_required
def excluir_usuario():
    registro_usuario_excluir = str(request.form.get('registro_usuario')).strip()
    registro_usuario_logado = str(session['user'].get('registro')).strip()

    if registro_usuario_excluir == registro_usuario_logado:
        flash('Você não pode excluir a si mesmo.', 'warning')
        return redirect(url_for('gerenciar_usuarios'))

    usuarios = carregar_usuarios()
    usuarios_atualizados = [
        u for u in usuarios 
        if str(u.get('registro') or u.get('username', '')).strip() != registro_usuario_excluir
    ]
    salvar_usuarios(usuarios_atualizados)
    
    flash('Usuário excluído com sucesso.', 'success')
    return redirect(url_for('gerenciar_usuarios'))

@app.route('/gerenciar_materiais_escritorio', methods=['GET', 'POST'])
@login_required
@gestor_required
def gerenciar_materiais_escritorio():
    if request.method == 'POST':
        codigo = request.form.get('codigo')
        descricao = request.form.get('descricao')
        quantidade_maxima = request.form.get('quantidade_maxima')

        materiais = carregar_materiais()
        novo_material = {
            "codigo": codigo,
            "descricao": descricao,
            "quantidade_maxima": int(quantidade_maxima),
            "categoria": "escritorio"
        }
        materiais.append(novo_material)
        salvar_materiais(materiais)
        flash('Material de escritório adicionado com sucesso!', 'success')
        return redirect(url_for('gerenciar_materiais_escritorio'))
        
    materiais = carregar_materiais()
    materiais_escritorio = [m for m in materiais if m.get('categoria', 'escritorio') == 'escritorio']
    return render_template('gerenciar_materiais_escritorio.html', materiais=materiais_escritorio)

@app.route('/gerenciar_materiais_epi', methods=['GET', 'POST'])
@login_required
@gestor_required
def gerenciar_materiais_epi():
    if request.method == 'POST':
        codigo = request.form.get('codigo')
        descricao = request.form.get('descricao')
        quantidade_maxima = request.form.get('quantidade_maxima')

        materiais = carregar_materiais()
        novo_material = {
            "codigo": codigo,
            "descricao": descricao,
            "quantidade_maxima": int(quantidade_maxima),
            "categoria": "epi"
        }
        materiais.append(novo_material)
        salvar_materiais(materiais)
        flash('EPI adicionado com sucesso!', 'success')
        return redirect(url_for('gerenciar_materiais_epi'))
        
    materiais = carregar_materiais()
    materiais_epi = [m for m in materiais if m.get('categoria') == 'epi']
    return render_template('gerenciar_materiais_epi.html', materiais=materiais_epi)

@app.route('/excluir_material', methods=['POST'])
@login_required
@gestor_required
def excluir_material():
    codigo_material_excluir = request.form.get('codigo_material')
    materiais = carregar_materiais()
    materiais_atualizados = [m for m in materiais if m['codigo'] != codigo_material_excluir]
    
    salvar_materiais(materiais_atualizados)
    flash('Material excluído com sucesso!', 'success')
    return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))

@app.route('/editar_material/<codigo_material>', methods=['GET', 'POST'])
@login_required
@gestor_required
def editar_material(codigo_material):
    materiais = carregar_materiais()
    material = next((m for m in materiais if m['codigo'] == codigo_material), None)

    if not material:
        flash('Material não encontrado.', 'danger')
        return redirect(url_for('gerenciar_materiais_escritorio'))

    if request.method == 'POST':
        nova_descricao = request.form.get('descricao')
        nova_quantidade_maxima = request.form.get('quantidade_maxima')

        material['descricao'] = nova_descricao
        material['quantidade_maxima'] = int(nova_quantidade_maxima)

        salvar_materiais(materiais)
        flash('Material atualizado com sucesso!', 'success')

        if material.get('categoria') == 'epi':
            return redirect(url_for('gerenciar_materiais_epi'))
        return redirect(url_for('gerenciar_materiais_escritorio'))

    return render_template('editar_material.html', material=material)

@app.route('/gerenciar_departamentos', methods=['GET', 'POST'])
@login_required
@admin_required
def gerenciar_departamentos():
    if request.method == 'POST':
        nome_departamento = request.form.get('nome', '').strip()
        departamentos = carregar_departamentos()

        if nome_departamento and nome_departamento not in departamentos:
            departamentos.append(nome_departamento)
            salvar_departamentos(departamentos)
            flash(f'Departamento "{nome_departamento}" adicionado com sucesso!', 'success')
        else:
            flash('Departamento já existe ou nome inválido.', 'warning')
            
        return redirect(url_for('gerenciar_departamentos'))

    departamentos = carregar_departamentos()
    return render_template('gerenciar_departamentos.html', departamentos=departamentos)

@app.route('/excluir_departamento', methods=['POST'])
@login_required
@admin_required
def excluir_departamento():
    nome_excluir = request.form.get('nome')
    departamentos = carregar_departamentos()

    if nome_excluir in departamentos:
        departamentos.remove(nome_excluir)
        salvar_departamentos(departamentos)
        flash(f'Departamento "{nome_excluir}" removido.', 'success')

    return redirect(url_for('gerenciar_departamentos'))

@app.route('/requisicao/<int:requisicao_id>')
@login_required
def ver_requisicao(requisicao_id):
    requisicoes = carregar_requisicoes()
    requisicao = next((r for r in requisicoes if r['id'] == requisicao_id), None)
    
    if not requisicao:
        flash('Requisição não encontrada.', 'danger')
        return redirect(url_for('historico'))
    
    usuario_logado = session['user']
    registro_logado = str(usuario_logado.get('registro', '')).strip().lower()
    permissao_logada = usuario_logado.get('permissao', '')
    
    registro_requisicao = str(requisicao.get('registro', '')).strip().lower()
    eh_dono = (registro_requisicao == registro_logado)
    
    if permissao_logada not in ['admin', 'administrador', 'abastecedor'] and not eh_dono:
        flash('Você não tem permissão para visualizar esta requisição.', 'danger')
        return redirect(url_for('historico'))

    return render_template('ver_requisicao.html', requisicao=requisicao)

@app.route('/importar_materiais_csv', methods=['POST'])
@login_required
@gestor_required
def importar_materiais_csv():
    categoria = request.form.get('categoria', 'escritorio')
    file = request.files.get('arquivo_csv')

    if not file or not file.filename:
        flash('Nenhum arquivo foi selecionado.', 'danger')
        return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))

    filename = file.filename.lower()
    materiais = carregar_materiais()
    codigos_existentes = {str(m['codigo']).strip().lower() for m in materiais}
    novos_cadastrados = 0

    try:
        if filename.endswith('.xlsx'):
            workbook = openpyxl.load_workbook(file, data_only=True)
            sheet = workbook.active
            rows = list(sheet.iter_rows(values_only=True))

            if not rows:
                flash('A planilha enviada está vazia.', 'warning')
                return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))

            header = [normalizar_texto(cell) for cell in rows[0]]

            idx_codigo = next((i for i, h in enumerate(header) if 'cod' in h), 0)
            idx_desc = next((i for i, h in enumerate(header) if 'desc' in h or 'nome' in h or 'item' in h), 1)
            idx_qtd = next((i for i, h in enumerate(header) if 'qtd' in h or 'quant' in h or 'max' in h), 2)

            for row in rows[1:]:
                if not row or len(row) <= idx_codigo:
                    continue
                
                codigo_raw = row[idx_codigo]
                if codigo_raw is None:
                    continue

                codigo = str(codigo_raw).strip()
                descricao = str(row[idx_desc]).strip() if len(row) > idx_desc and row[idx_desc] is not None else ""
                qtd_max_raw = row[idx_qtd] if len(row) > idx_qtd and row[idx_qtd] is not None else 1

                try:
                    qtd_max = int(float(str(qtd_max_raw).replace(',', '.')))
                except ValueError:
                    qtd_max = 1

                if codigo and descricao and (codigo.lower() not in codigos_existentes):
                    materiais.append({
                        "codigo": codigo,
                        "descricao": descricao,
                        "quantidade_maxima": qtd_max,
                        "categoria": categoria
                    })
                    codigos_existentes.add(codigo.lower())
                    novos_cadastrados += 1

        elif filename.endswith('.csv'):
            content = file.stream.read().decode("utf-8-sig", errors="ignore")
            delimiter = ';' if ';' in content else ','
            stream = io.StringIO(content)
            reader = csv.reader(stream, delimiter=delimiter)
            rows = list(reader)

            if not rows:
                flash('O arquivo CSV está vazio.', 'warning')
                return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))

            header = [normalizar_texto(cell) for cell in rows[0]]

            idx_codigo = next((i for i, h in enumerate(header) if 'cod' in h), 0)
            idx_desc = next((i for i, h in enumerate(header) if 'desc' in h or 'nome' in h or 'item' in h), 1)
            idx_qtd = next((i for i, h in enumerate(header) if 'qtd' in h or 'quant' in h or 'max' in h), 2)

            for row in rows[1:]:
                if not row or len(row) <= idx_codigo:
                    continue

                codigo = str(row[idx_codigo]).strip()
                descricao = str(row[idx_desc]).strip() if len(row) > idx_desc else ""
                qtd_max_raw = row[idx_qtd] if len(row) > idx_qtd else 1

                try:
                    qtd_max = int(float(str(qtd_max_raw).replace(',', '.')))
                except ValueError:
                    qtd_max = 1

                if codigo and descricao and (codigo.lower() not in codigos_existentes):
                    materiais.append({
                        "codigo": codigo,
                        "descricao": descricao,
                        "quantidade_maxima": qtd_max,
                        "categoria": categoria
                    })
                    codigos_existentes.add(codigo.lower())
                    novos_cadastrados += 1

        else:
            flash('Formato inválido. Por favor, envie um arquivo .csv ou .xlsx (Excel).', 'danger')
            return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))

        salvar_materiais(materiais)
        flash(f'Sucesso! {novos_cadastrados} novos materiais foram importados.', 'success')

    except Exception as e:
        flash(f'Erro ao processar o arquivo: {str(e)}', 'danger')

    return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))

if __name__ == '__main__':
    app.run(debug=True)